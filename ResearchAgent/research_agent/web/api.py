from __future__ import annotations

from collections import Counter
import threading
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from research_agent.config import Settings
from research_agent.services.job_manager import JobManager
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.markdown_renderer import render_markdown
from research_agent.services.manual_ingest import ManualIngestService
from research_agent.services.storage_manager import StorageManager


class URLIngestRequest(BaseModel):
    url: str


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    storage_manager = StorageManager(app_settings.data_dir)
    llm_processor = LLMProcessor(app_settings)
    manual_ingest = ManualIngestService(app_settings, storage_manager, llm_processor)
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
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    def build_article_payload(article: dict) -> dict:
        source_files = [
            {
                **entry,
                "url": f"/files/{entry['path']}",
            }
            for entry in article.get("source_files", [])
        ]
        return {
            **article,
            "source_files": source_files,
            "rendered_html": render_markdown(article.get("markdown", "")),
        }

    @app.get("/api/library")
    async def library() -> dict:
        articles = storage_manager.scan_library()
        dates = sorted({row.get("archive_date", "") for row in articles if row.get("archive_date")}, reverse=True)
        topic_counter: Counter[str] = Counter()
        for article in articles:
            topic_counter.update(article.get("tags", []))
        return {
            "articles": articles,
            "dates": dates,
            "topics": [{"name": topic, "count": count} for topic, count in topic_counter.most_common(24)],
        }

    @app.get("/api/articles/{article_id}")
    async def article_detail(article_id: str) -> dict:
        article = storage_manager.load_article(article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        return build_article_payload(article)

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
