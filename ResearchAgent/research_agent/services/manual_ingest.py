from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

from research_agent.config import Settings
from research_agent.models import ResearchItem
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.storage_manager import StorageManager
from research_agent.services.webpage_capture import WebPageCaptureService


LOGGER = logging.getLogger(__name__)

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")


class ManualIngestService:
    def __init__(
        self,
        settings: Settings,
        storage_manager: StorageManager,
        llm_processor: LLMProcessor,
    ) -> None:
        self.settings = settings
        self.storage_manager = storage_manager
        self.llm_processor = llm_processor
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ResearchAgent/1.0 (+https://localhost)",
                "Accept": "application/json, text/html, application/xml;q=0.9, */*;q=0.8",
            }
        )
        self.page_capture = WebPageCaptureService(self.session)

    def ingest_url(self, url: str) -> dict:
        normalized_url = url.strip()
        if not normalized_url:
            raise ValueError("URL cannot be empty")

        arxiv_id = self.extract_arxiv_id(normalized_url)
        if arxiv_id:
            return self._ingest_arxiv(arxiv_id)
        return self._ingest_webpage(normalized_url)

    def ingest_pdf(self, filename: str, payload: bytes) -> dict:
        safe_name = Path(filename or "uploaded-document.pdf").name
        item = ResearchItem(
            source="upload",
            title=Path(safe_name).stem.replace("_", " ").replace("-", " ").strip() or "Uploaded PDF",
            summary="用户手动上传的 PDF 文档，已进入 Gemini 深度阅读流程。",
            source_url="",
            published_at=datetime.now().isoformat(timespec="seconds"),
            identifier=f"upload-{uuid.uuid4().hex[:10]}",
            tags=["manual-upload", "pdf"],
            meta={"upload_filename": safe_name},
        )
        stored_item = self.storage_manager.persist_item(item, {"source.pdf": payload})
        article = self.llm_processor.generate_article(stored_item)
        self.storage_manager.write_article(stored_item, article)
        return self._build_article_response(stored_item.metadata_path)

    def _ingest_arxiv(self, arxiv_id: str) -> dict:
        metadata = self._fetch_arxiv_metadata(arxiv_id)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"
        source_files = {
            "source.pdf": self._download_binary(pdf_url),
            "source.html": self._download_text(abs_url).encode("utf-8"),
        }
        item = ResearchItem(
            source="manual-arxiv",
            title=metadata["title"],
            summary=metadata["summary"],
            source_url=abs_url,
            html_url=abs_url,
            pdf_url=pdf_url,
            published_at=metadata["published_at"],
            identifier=arxiv_id,
            authors=metadata["authors"],
            tags=["manual-upload", "arxiv", *metadata["tags"]],
        )
        stored_item = self.storage_manager.persist_item(item, source_files)
        article = self.llm_processor.generate_article(stored_item)
        self.storage_manager.write_article(stored_item, article)
        return self._build_article_response(stored_item.metadata_path)

    def _ingest_webpage(self, url: str) -> dict:
        captured = self.page_capture.capture(url)
        summary = captured.text[:500].strip() or f"网页内容：{captured.final_url}"
        item = ResearchItem(
            source="manual-web",
            title=captured.title or captured.final_url,
            summary=summary,
            source_url=captured.final_url,
            html_url=captured.final_url,
            published_at=datetime.now().isoformat(timespec="seconds"),
            identifier=f"web-{uuid.uuid4().hex[:10]}",
            tags=["manual-upload", "webpage"],
            meta={
                "used_browser_render": captured.used_browser,
                "image_urls": captured.image_urls,
            },
        )
        stored_item = self.storage_manager.persist_item(item, captured.source_files)
        article = self.llm_processor.generate_article(stored_item)
        self.storage_manager.write_article(stored_item, article)
        return self._build_article_response(stored_item.metadata_path)

    def _fetch_arxiv_metadata(self, arxiv_id: str) -> dict:
        response = self.session.get(
            "https://export.arxiv.org/api/query",
            params={"id_list": arxiv_id},
            timeout=30,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        if not feed.entries:
            raise ValueError(f"Unable to resolve arXiv entry: {arxiv_id}")
        entry = feed.entries[0]
        return {
            "title": self._clean_text(entry.title),
            "summary": self._clean_text(entry.summary),
            "authors": [author.name for author in getattr(entry, "authors", [])],
            "tags": [tag.term for tag in getattr(entry, "tags", [])],
            "published_at": getattr(entry, "published", datetime.now().isoformat(timespec="seconds")),
        }

    def _download_binary(self, url: str) -> bytes:
        response = self.session.get(url, timeout=60)
        response.raise_for_status()
        return response.content

    def _download_text(self, url: str) -> str:
        response = self.session.get(url, timeout=60)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text

    def _build_article_response(self, metadata_path: Path) -> dict:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        article = self.storage_manager.load_article(metadata["article_id"])
        if article is None:
            raise RuntimeError("Article was stored but could not be loaded")
        return article

    @staticmethod
    def extract_arxiv_id(url: str) -> str | None:
        parsed = urlparse(url)
        if "arxiv.org" not in parsed.netloc.lower():
            return None
        match = ARXIV_ID_RE.search(url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()
