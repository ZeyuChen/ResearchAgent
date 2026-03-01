from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class IngestJob:
    job_id: str
    kind: str
    filename: str = ""
    status: str = "queued"
    progress: int = 0
    message: str = "任务已创建，等待执行。"
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    article: dict[str, Any] | None = None


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, IngestJob] = {}
        self._lock = threading.Lock()

    def create_job(self, kind: str, filename: str = "") -> IngestJob:
        job = IngestJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            kind=kind,
            filename=filename,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def update(self, job_id: str, *, status: str | None = None, progress: int | None = None, message: str | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0, min(100, progress))
            if message is not None:
                job.message = message
            job.updated_at = datetime.now().isoformat(timespec="seconds")

    def complete(self, job_id: str, article: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.progress = 100
            job.message = "任务完成。"
            job.article = article
            job.updated_at = datetime.now().isoformat(timespec="seconds")

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.error = error
            job.message = error
            job.updated_at = datetime.now().isoformat(timespec="seconds")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {
                "job_id": job.job_id,
                "kind": job.kind,
                "filename": job.filename,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "article": job.article,
            }
