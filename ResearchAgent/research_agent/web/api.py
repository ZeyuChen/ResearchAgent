from __future__ import annotations

import threading
import re
from collections import Counter
from typing import Annotated
from urllib.parse import quote_plus

import feedparser
import requests

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from research_agent.config import Settings
from research_agent.services.arxiv_source_gallery import ArxivSourceGalleryService
from research_agent.services.chat_service import ChatService
from research_agent.services.job_manager import JobManager
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.markdown_renderer import extract_pdf_page_refs, inject_pdf_page_links, render_markdown
from research_agent.services.manual_ingest import ManualIngestService
from research_agent.services.pdf_preview import PDFPreviewService
from research_agent.services.storage_manager import StorageManager

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")
VISIBLE_SOURCE_FILES = {"rendered-page.png", "source.html"}


class URLIngestRequest(BaseModel):
    url: str


class ChatMessageRequest(BaseModel):
    article_id: str
    message: str
    model: str = "flash"
    session_id: str | None = None
    new_session: bool = False


class FlomoSaveRequest(BaseModel):
    content: str
    article_id: str | None = None
    source_kind: str = "selection"
    formatted: bool = False


def build_display_tags(article: dict) -> list[str]:
    return [str(tag).strip() for tag in article.get("topic_tags", []) if str(tag).strip()][:6]


def build_visible_source_files(source_files: list[dict]) -> list[dict]:
    return [entry for entry in source_files if entry.get("name") in VISIBLE_SOURCE_FILES]


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    storage_manager = StorageManager(app_settings.data_dir)
    llm_processor = LLMProcessor(app_settings)
    manual_ingest = ManualIngestService(app_settings, storage_manager, llm_processor)
    chat_service = ChatService(app_settings, storage_manager, llm_processor)
    pdf_preview_service = PDFPreviewService(app_settings.data_dir)
    arxiv_source_gallery_service = ArxivSourceGalleryService(app_settings.data_dir)
    job_manager = JobManager()
    static_dir = app_settings.project_root / "research_agent" / "web" / "static"

    app = FastAPI(
        title="ResearchAgent WebUI",
        description="Local RL research knowledge base",
        version="1.0.0",
    )

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/files", StaticFiles(directory=app_settings.data_dir), name="files")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "flomo_configured": bool(app_settings.flomo_webhook_url)}

    @app.get("/api/chat/options")
    async def chat_options() -> dict:
        return {
            "available": chat_service.available(),
            "default_model_key": chat_service.default_model_key(),
            "models": chat_service.model_catalog(),
        }

    def normalize_flomo_body(text: str) -> str:
        normalized_lines: list[str] = []
        for raw_line in str(text or "").replace("\r\n", "\n").split("\n"):
            line = " ".join(raw_line.split()).strip()
            if line:
                normalized_lines.append(line)
                continue
            if normalized_lines and normalized_lines[-1] != "":
                normalized_lines.append("")
        while normalized_lines and normalized_lines[0] == "":
            normalized_lines.pop(0)
        while normalized_lines and normalized_lines[-1] == "":
            normalized_lines.pop()
        return "\n".join(normalized_lines).strip()

    def format_flomo_payload(text: str, article: dict | None, source_kind: str) -> str:
        body = normalize_flomo_body(text)
        if not body:
            raise ValueError("Empty content cannot be saved")

        sections: list[str] = []
        tags: list[str] = []
        if article:
            arxiv_id = infer_arxiv_id(article)
            if arxiv_id:
                tags.append(f"#arxiv/{arxiv_id}")

            topic_tags = [str(tag).strip().lstrip("#") for tag in article.get("topic_tags", []) if str(tag).strip()]
            if topic_tags:
                tags.extend(f"#{tag}" for tag in topic_tags[:8])

        if tags:
            sections.append(" ".join(tags))

        if source_kind == "chat":
            sections.append("问答摘录")
        elif source_kind == "summary":
            sections.append("阅读摘要")

        sections.append(body)
        return "\n\n".join(section for section in sections if section.strip())

    def save_to_flomo(text: str, article: dict | None, source_kind: str, formatted: bool = False) -> dict:
        if not app_settings.flomo_webhook_url:
            raise RuntimeError("Flomo webhook is not configured")

        content = normalize_flomo_body(text) if formatted else format_flomo_payload(text, article, source_kind)
        if not content:
            raise ValueError("Empty content cannot be saved")
        response = requests.post(
            app_settings.flomo_webhook_url,
            json={"content": content},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        return {"ok": True, "content_preview": content[:200]}

    def search_arxiv_by_title(query: str, limit: int = 6) -> list[dict]:
        normalized_query = " ".join(query.split()).strip()
        if len(normalized_query) < 3:
            return []
        request_url = (
            "https://export.arxiv.org/api/query"
            f"?search_query=ti:{quote_plus(normalized_query)}"
            f"&sortBy=relevance&sortOrder=descending&max_results={limit}"
        )
        response = requests.get(
            request_url,
            timeout=15,
            headers={"User-Agent": "ResearchAgent/1.0 (+https://localhost)"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        results: list[dict] = []
        for entry in feed.entries:
            arxiv_id = ""
            match = ARXIV_ID_RE.search(str(getattr(entry, "id", "")) or "")
            if match:
                arxiv_id = match.group(1)
            if not arxiv_id:
                continue
            results.append(
                {
                    "title": " ".join(str(getattr(entry, "title", "")).split()),
                    "summary": " ".join(str(getattr(entry, "summary", "")).split()),
                    "arxiv_id": arxiv_id,
                    "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "published_at": str(getattr(entry, "published", "")),
                }
            )
        return results

    def infer_arxiv_id(article: dict) -> str:
        for candidate in (
            article.get("identifier", ""),
            article.get("source_url", ""),
            article.get("meta", {}).get("arxiv_id", ""),
        ):
            if not candidate:
                continue
            match = ARXIV_ID_RE.search(str(candidate))
            if match:
                return match.group(1)
        return ""

    def build_article_payload(article: dict) -> dict:
        source_files = [
            {
                **entry,
                "url": f"/files/{entry['path']}",
            }
            for entry in article.get("source_files", [])
        ]
        display_tags = build_display_tags(article)
        pdf_source = next((entry["url"] for entry in source_files if entry.get("name") == "source.pdf"), "")
        rendered_html = render_markdown(article.get("markdown", ""))
        rendered_html = inject_pdf_page_links(rendered_html, pdf_source or None)
        item_dir = (app_settings.data_dir / article["article_path"]).parent if article.get("article_path") else None

        source_gallery: list[dict] = []
        if item_dir:
            arxiv_gallery_entries = arxiv_source_gallery_service.ensure_gallery(item_dir, infer_arxiv_id(article))
            source_gallery = [
                {
                    **entry,
                    "url": f"/files/{entry['path']}",
                }
                for entry in arxiv_gallery_entries
            ]

        previews: list[dict] = []
        if pdf_source and item_dir:
            pdf_path = item_dir / "source.pdf"
            preview_entries = pdf_preview_service.ensure_previews(pdf_path, extract_pdf_page_refs(article.get("markdown", "")))
            previews = [
                {
                    **entry,
                    "url": f"/files/{entry['path']}",
                }
                for entry in preview_entries
            ]
        return {
            **article,
            "source_files": source_files,
            "display_source_files": build_visible_source_files(source_files),
            "display_tags": display_tags,
            "pdf_source_url": pdf_source,
            "pdf_page_refs": extract_pdf_page_refs(article.get("markdown", "")),
            "source_figure_gallery": source_gallery,
            "pdf_previews": previews,
            "rendered_html": rendered_html,
        }

    @app.get("/api/library")
    async def library() -> dict:
        articles = storage_manager.scan_library()
        topic_counter: Counter[str] = Counter()
        for article in articles:
            article["display_tags"] = build_display_tags(article)
            article["arxiv_id"] = infer_arxiv_id(article)
            topic_counter.update(article["display_tags"])
        return {
            "articles": articles,
            "topics": [{"name": topic, "count": count} for topic, count in topic_counter.most_common(24)],
        }

    @app.get("/api/search/arxiv")
    async def arxiv_search(q: str) -> dict:
        try:
            results = search_arxiv_by_title(q)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"arXiv search failed: {exc}") from exc
        return {"results": results}

    @app.get("/api/articles/{article_id}")
    async def article_detail(article_id: str) -> dict:
        article = storage_manager.load_article(article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        return build_article_payload(article)

    @app.post("/api/integrations/flomo/preview")
    async def flomo_preview(payload: FlomoSaveRequest) -> dict:
        article = None
        if payload.article_id:
            article = storage_manager.load_article(payload.article_id)
            if not article:
                raise HTTPException(status_code=404, detail="Article not found")
        try:
            content = normalize_flomo_body(payload.content) if payload.formatted else format_flomo_payload(
                payload.content,
                article,
                payload.source_kind,
            )
            return {"content": content}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/integrations/flomo/save")
    async def flomo_save(payload: FlomoSaveRequest) -> dict:
        article = None
        if payload.article_id:
            article = storage_manager.load_article(payload.article_id)
            if not article:
                raise HTTPException(status_code=404, detail="Article not found")
        try:
            return save_to_flomo(payload.content, article, payload.source_kind, formatted=payload.formatted)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Flomo save failed: {exc}") from exc

    @app.post("/api/chat/messages")
    async def chat_messages(payload: ChatMessageRequest) -> dict:
        article = storage_manager.load_article(payload.article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        try:
            return chat_service.send_message(
                article=article,
                article_id=payload.article_id,
                message=payload.message,
                model_key=payload.model,
                session_id=payload.session_id,
                force_new_session=payload.new_session,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/chat/session")
    async def chat_session(article_id: str, model: str = "flash", session_id: str | None = None) -> dict:
        return chat_service.get_session(article_id=article_id, model_key=model, session_id=session_id)

    def start_job(job_kind: str, worker, filename: str = "") -> dict:
        job = job_manager.create_job(job_kind, filename=filename)

        def run() -> None:
            try:
                job_manager.update(job.job_id, status="running", progress=3, message="任务已开始。")
                article = worker(
                    lambda progress, message: job_manager.update(
                        job.job_id,
                        status="running",
                        progress=progress,
                        message=message,
                    )
                )
                job_manager.complete(job.job_id, build_article_payload(article))
            except Exception as exc:
                job_manager.fail(job.job_id, str(exc))

        threading.Thread(target=run, daemon=True).start()
        return {
            "job_id": job.job_id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
        }

    @app.post("/api/ingest/url")
    async def ingest_url(payload: URLIngestRequest) -> dict:
        if not payload.url.strip():
            raise HTTPException(status_code=400, detail="URL cannot be empty")
        return start_job(
            "url",
            lambda progress_callback: manual_ingest.ingest_url(payload.url, progress_callback=progress_callback),
        )

    @app.post("/api/ingest/pdf")
    async def ingest_pdf(file: Annotated[UploadFile, File(...)]) -> dict:
        filename = file.filename or "uploaded-document.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF uploads are supported")
        payload = await file.read()
        if not payload:
            raise HTTPException(status_code=400, detail="Uploaded PDF is empty")
        return start_job(
            "pdf",
            lambda progress_callback: manual_ingest.ingest_pdf(filename, payload, progress_callback=progress_callback),
            filename=filename,
        )

    @app.get("/api/ingest/jobs/{job_id}")
    async def ingest_job(job_id: str) -> dict:
        job = job_manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    return app


app = create_app()
