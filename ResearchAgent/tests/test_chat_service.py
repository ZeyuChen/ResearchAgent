from __future__ import annotations

from research_agent.config import Settings
from research_agent.services.chat_service import ChatService
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.storage_manager import StorageManager


def test_chat_service_exposes_current_models(tmp_path) -> None:
    settings = Settings.from_env()
    settings.gemini_api_key = None
    settings.data_dir = tmp_path
    service = ChatService(
        settings=settings,
        storage_manager=StorageManager(tmp_path),
        llm_processor=LLMProcessor(settings),
    )

    catalog = service.model_catalog()

    assert service.default_model_key() == "flash"
    assert [entry["key"] for entry in catalog] == ["flash", "pro"]
    assert catalog[0]["api_name"] == "gemini-3-flash-preview"
    assert catalog[1]["api_name"] == "gemini-3.1-pro-preview"
