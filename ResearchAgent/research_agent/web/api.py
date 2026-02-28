from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from research_agent.config import Settings
from research_agent.services.markdown_renderer import render_markdown
from research_agent.services.storage_manager import StorageManager


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    storage_manager = StorageManager(app_settings.data_dir)
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

    return app


app = create_app()
