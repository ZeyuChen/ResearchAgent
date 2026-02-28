from __future__ import annotations

import logging

from research_agent.config import Settings
from research_agent.services.data_fetcher import DataFetcher
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.storage_manager import StorageManager


LOGGER = logging.getLogger(__name__)


class ResearchPipeline:
    def __init__(
        self,
        settings: Settings,
        storage_manager: StorageManager,
        data_fetcher: DataFetcher,
        llm_processor: LLMProcessor,
    ) -> None:
        self.settings = settings
        self.storage_manager = storage_manager
        self.data_fetcher = data_fetcher
        self.llm_processor = llm_processor

    @classmethod
    def from_settings(cls, settings: Settings) -> "ResearchPipeline":
        storage_manager = StorageManager(settings.data_dir)
        llm_processor = LLMProcessor(settings)
        data_fetcher = DataFetcher(
            settings=settings,
            llm_filter=llm_processor.summary_is_relevant if settings.enable_llm_filter else None,
        )
        return cls(settings, storage_manager, data_fetcher, llm_processor)

    def run_once(self, limit: int | None = None) -> list[dict]:
        items = self.data_fetcher.fetch_all()
        if limit is not None:
            items = items[:limit]

        processed: list[dict] = []
        for item in items:
            try:
                source_files = self.data_fetcher.download_source_files(item)
                stored_item = self.storage_manager.persist_item(item, source_files)
                article = self.llm_processor.generate_article(stored_item)
                self.storage_manager.write_article(stored_item, article)
                processed.append(
                    {
                        "article_id": stored_item.item_dir.name,
                        "title": item.title,
                        "path": stored_item.item_dir.as_posix(),
                        "source": item.source,
                    }
                )
            except Exception as exc:
                LOGGER.exception("Failed to process item %s: %s", item.title, exc)
        return processed
