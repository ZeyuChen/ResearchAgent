from __future__ import annotations

from research_agent.config import Settings
from research_agent.services.chat_service import ChatContextHandle, ChatService, ChatSession
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


def test_chat_service_serializes_assistant_markdown(tmp_path) -> None:
    settings = Settings.from_env()
    settings.gemini_api_key = None
    settings.data_dir = tmp_path
    service = ChatService(
        settings=settings,
        storage_manager=StorageManager(tmp_path),
        llm_processor=LLMProcessor(settings),
    )
    session = ChatSession(
        session_id="chat-test",
        article_id="article-1",
        model_key="flash",
        model_name="gemini-3-flash-preview",
        title="Title",
        created_at="2026-03-01T00:00:00",
        updated_at="2026-03-01T00:00:00",
        messages=[
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "## Heading\n\n- item"},
        ],
        context=ChatContextHandle(cache_status="ready", cache_kind="pdf"),
    )

    payload = service._serialize_session(session)

    assert "rendered_html" not in payload["messages"][0]
    assert "<h2>Heading</h2>" in payload["messages"][1]["rendered_html"]
    assert "<li>item</li>" in payload["messages"][1]["rendered_html"]
