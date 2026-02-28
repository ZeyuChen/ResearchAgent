import json
from pathlib import Path
from tempfile import TemporaryDirectory

from research_agent.models import ResearchItem
from research_agent.services.storage_manager import StorageManager


def test_storage_manager_persists_metadata_and_article() -> None:
    with TemporaryDirectory() as temp_dir:
        manager = StorageManager(Path(temp_dir))
        item = ResearchItem(
            source="arxiv",
            title="Test PPO Infrastructure",
            summary="A short summary about PPO and distributed training.",
            source_url="https://example.com",
            html_url="https://example.com",
            pdf_url="https://example.com/test.pdf",
            published_at="2026-03-01T09:00:00",
            identifier="test-id",
            tags=["ppo", "distributed training"],
        )

        stored = manager.persist_item(item, {"source.html": b"<html></html>"})
        manager.write_article(stored, "# Test")
        metadata = json.loads(stored.metadata_path.read_text(encoding="utf-8"))

        assert stored.metadata_path.exists()
        assert stored.article_path.exists()
        assert manager.load_article(metadata["article_id"]) is not None
