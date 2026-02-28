from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
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
ProgressCallback = Callable[[int, str], None]


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

    def ingest_url(self, url: str, progress_callback: ProgressCallback | None = None) -> dict:
        normalized_url = url.strip()
        if not normalized_url:
            raise ValueError("URL cannot be empty")

        self._notify(progress_callback, 8, "已收到链接，正在识别内容类型。")
        arxiv_id = self.extract_arxiv_id(normalized_url)
        if arxiv_id:
            return self._ingest_arxiv(arxiv_id, progress_callback=progress_callback)
        return self._ingest_webpage(normalized_url, progress_callback=progress_callback)

    def ingest_pdf(
        self,
        filename: str,
        payload: bytes,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        safe_name = Path(filename or "uploaded-document.pdf").name
        self._notify(progress_callback, 10, "上传完成，正在建立本地归档目录。")
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
        self._notify(progress_callback, 28, "PDF 已归档，准备调用 Gemini 解析。")
        article, usage = self.llm_processor.generate_article_with_metrics(stored_item, progress_callback=progress_callback)
        self.storage_manager.write_article(stored_item, article)
        self.storage_manager.update_metadata(
            stored_item.metadata_path,
            {
                "llm_usage": usage,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self._notify(progress_callback, 96, "解析完成，正在整理输出结果。")
        return self._build_article_response(stored_item.metadata_path)

    def _ingest_arxiv(
        self,
        arxiv_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        self._notify(progress_callback, 14, "已识别为 arXiv 链接，正在拉取论文元数据。")
        metadata = self._fetch_arxiv_metadata(arxiv_id)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"
        self._notify(progress_callback, 26, "正在下载 arXiv PDF 与摘要页面。")
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
        self._notify(progress_callback, 36, "论文已归档，准备进入 Gemini 全文阅读。")
        article, usage = self.llm_processor.generate_article_with_metrics(stored_item, progress_callback=progress_callback)
        self.storage_manager.write_article(stored_item, article)
        self.storage_manager.update_metadata(
            stored_item.metadata_path,
            {
                "llm_usage": usage,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self._notify(progress_callback, 96, "arXiv 论文解析完成，正在生成展示数据。")
        return self._build_article_response(stored_item.metadata_path)

    def _ingest_webpage(
        self,
        url: str,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        self._notify(progress_callback, 14, "正在加载网页，优先尝试浏览器渲染动态内容。")
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
        self._notify(progress_callback, 34, "网页素材已归档，准备调用 Gemini 进行多模态解读。")
        article, usage = self.llm_processor.generate_article_with_metrics(stored_item, progress_callback=progress_callback)
        self.storage_manager.write_article(stored_item, article)
        self.storage_manager.update_metadata(
            stored_item.metadata_path,
            {
                "llm_usage": usage,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self._notify(progress_callback, 96, "网页解析完成，正在生成展示数据。")
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

    @staticmethod
    def _notify(progress_callback: ProgressCallback | None, progress: int, message: str) -> None:
        if progress_callback:
            progress_callback(progress, message)
