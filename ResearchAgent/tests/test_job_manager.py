from research_agent.services.job_manager import JobManager


def test_job_manager_tracks_complete_lifecycle() -> None:
    manager = JobManager()
    job = manager.create_job("pdf", filename="demo.pdf")
    manager.update(job.job_id, status="running", progress=42, message="running")
    manager.complete(job.job_id, {"article_id": "abc"})

    snapshot = manager.get(job.job_id)
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    assert snapshot["progress"] == 100
    assert snapshot["article"]["article_id"] == "abc"
