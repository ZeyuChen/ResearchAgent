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
                summary_usage = self.llm_processor._empty_usage()
                if item.source == "arxiv" and item.summary:
                    item.meta["source_abstract"] = item.summary
                    item.summary, summary_usage = self.llm_processor.translate_arxiv_summary(item.summary, item.title)
                source_files = self.data_fetcher.download_source_files(item)
                stored_item = self.storage_manager.persist_item(item, source_files)
                article, usage = self.llm_processor.generate_article_with_metrics(stored_item)
                seed_text = article
                if item.source == "arxiv" and item.summary:
                    seed_text = f"{item.title}\n\n{item.summary}"
                topic_tags, tag_usage = self.llm_processor.generate_topic_tags(seed_text, stored_item.item)
                usage = self.llm_processor.merge_usage(summary_usage, usage, tag_usage)
                self.storage_manager.write_article(stored_item, article)
                metadata = self.storage_manager.update_metadata(
                    stored_item.metadata_path,
                    {
                        "summary": item.summary,
                        "topic_tags": topic_tags,
                        "llm_usage": usage,
                    },
                )
                processed.append(
                    {
                        "article_id": metadata["article_id"],
                        "title": item.title,
                        "path": stored_item.item_dir.as_posix(),
                        "source": item.source,
                    }
                )
            except Exception as exc:
                LOGGER.exception("Failed to process item %s: %s", item.title, exc)
        return processed
