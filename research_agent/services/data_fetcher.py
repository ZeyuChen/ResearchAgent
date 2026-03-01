from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Callable
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

from research_agent.config import Settings
from research_agent.models import ResearchItem


LOGGER = logging.getLogger(__name__)


class DataFetcher:
    def __init__(
        self,
        settings: Settings,
        llm_filter: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.llm_filter = llm_filter
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ResearchAgent/1.0 (+https://localhost)",
                "Accept": "application/json, text/html, application/xml;q=0.9, */*;q=0.8",
            }
        )
        if settings.github_token:
            self.session.headers["Authorization"] = f"Bearer {settings.github_token}"

    def fetch_all(self) -> list[ResearchItem]:
        candidates: list[ResearchItem] = []
        for loader in (self.fetch_arxiv, self.fetch_github_releases, self.fetch_huggingface_papers):
            try:
                candidates.extend(loader())
            except Exception as exc:
                LOGGER.warning("Source fetch failed for %s: %s", loader.__name__, exc)
        filtered = [item for item in candidates if self._accept_item(item)]
        return self._dedupe(filtered)

    def fetch_arxiv(self) -> list[ResearchItem]:
        query = quote_plus("(cat:cs.AI OR cat:cs.LG)")
        url = (
            "https://export.arxiv.org/api/query"
            f"?search_query={query}&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={self.settings.max_arxiv_results}"
        )
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        items: list[ResearchItem] = []
        for entry in feed.entries:
            authors = [author.name for author in getattr(entry, "authors", [])]
            tags = [tag.term for tag in getattr(entry, "tags", [])]
            pdf_url = self._extract_arxiv_pdf_url(entry)
            items.append(
                ResearchItem(
                    source="arxiv",
                    title=self._clean_text(entry.title),
                    summary=self._clean_text(entry.summary),
                    source_url=entry.link,
                    html_url=entry.link,
                    pdf_url=pdf_url,
                    published_at=getattr(entry, "published", ""),
                    identifier=entry.id.rsplit("/", 1)[-1],
                    authors=authors,
                    tags=tags,
                )
            )
        return items

    def fetch_github_releases(self) -> list[ResearchItem]:
        items: list[ResearchItem] = []
        for repo in self.settings.tracked_github_repos:
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            response = self.session.get(url, timeout=30)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()
            title = f"{repo} {payload.get('tag_name', '').strip()} {payload.get('name', '').strip()}".strip()
            body = payload.get("body") or "GitHub release without release notes."
            items.append(
                ResearchItem(
                    source="github",
                    title=title,
                    summary=self._clean_text(body[:4000]),
                    source_url=payload.get("html_url") or f"https://github.com/{repo}",
                    html_url=payload.get("html_url") or f"https://github.com/{repo}",
                    published_at=payload.get("published_at") or payload.get("created_at") or "",
                    identifier=repo.replace("/", "-"),
                    authors=[payload.get("author", {}).get("login", "github")],
                    tags=[repo, "release"],
                    meta={
                        "repo": repo,
                        "tag_name": payload.get("tag_name"),
                    },
                )
            )
        return items

    def fetch_huggingface_papers(self) -> list[ResearchItem]:
        url = f"https://huggingface.co/papers?date={datetime.now().date().isoformat()}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[ResearchItem] = []
        seen_urls: set[str] = set()

        for article in soup.select("article"):
            link = article.select_one("a[href^='/papers/']")
            if not link:
                continue
            href = link.get("href", "")
            full_url = f"https://huggingface.co{href}"
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            title = self._clean_text(link.get_text(" ", strip=True))
            summary_node = article.select_one("p")
            summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else title)
            arxiv_anchor = article.select_one("a[href*='arxiv.org/abs/']")
            pdf_url = None
            if arxiv_anchor and arxiv_anchor.get("href"):
                pdf_url = arxiv_anchor["href"].replace("/abs/", "/pdf/") + ".pdf"
            items.append(
                ResearchItem(
                    source="huggingface",
                    title=title,
                    summary=summary,
                    source_url=full_url,
                    html_url=full_url,
                    pdf_url=pdf_url,
                    published_at=datetime.now().isoformat(timespec="seconds"),
                    identifier=href.rstrip("/").rsplit("/", 1)[-1],
                    tags=["huggingface", "daily-paper"],
                )
            )
        return items

    def download_source_files(self, item: ResearchItem) -> dict[str, bytes]:
        downloaded: dict[str, bytes] = {}
        if item.pdf_url:
            pdf_content = self._download_binary(item.pdf_url)
            if pdf_content:
                downloaded["source.pdf"] = pdf_content
        html_target = item.html_url or item.source_url
        if html_target:
            html_content = self._download_text(html_target)
            if html_content:
                downloaded["source.html"] = html_content.encode("utf-8")
        return downloaded

    def _accept_item(self, item: ResearchItem) -> bool:
        blob = f"{item.title}\n{item.summary}".lower()
        if not any(keyword.lower() in blob for keyword in self.settings.keywords):
            return False
        if self.llm_filter:
            try:
                return self.llm_filter(item.title, item.summary)
            except Exception as exc:
                LOGGER.warning("Secondary LLM filter failed for %s: %s", item.title, exc)
        return True

    @staticmethod
    def _dedupe(items: list[ResearchItem]) -> list[ResearchItem]:
        deduped: list[ResearchItem] = []
        seen: set[str] = set()
        for item in items:
            key = f"{item.source}:{item.identifier}:{item.source_url}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _download_binary(self, url: str) -> bytes | None:
        try:
            response = self.session.get(url, timeout=60)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            LOGGER.warning("Binary download failed for %s: %s", url, exc)
            return None

    def _download_text(self, url: str) -> str | None:
        try:
            response = self.session.get(url, timeout=60)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text
        except Exception as exc:
            LOGGER.warning("Text download failed for %s: %s", url, exc)
            return None

    @staticmethod
    def _extract_arxiv_pdf_url(entry: feedparser.FeedParserDict) -> str:
        for link in getattr(entry, "links", []):
            if link.get("title") == "pdf":
                return link["href"]
        return entry.link.replace("/abs/", "/pdf/") + ".pdf"

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()
