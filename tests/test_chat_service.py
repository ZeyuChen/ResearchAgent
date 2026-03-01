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


def test_chat_service_persists_latest_session(tmp_path) -> None:
    settings = Settings.from_env()
    settings.gemini_api_key = None
    settings.data_dir = tmp_path
    storage = StorageManager(tmp_path)

    service = ChatService(
        settings=settings,
        storage_manager=storage,
        llm_processor=LLMProcessor(settings),
    )
    session = ChatSession(
        session_id="chat-persist",
        article_id="article-1",
        model_key="flash",
        model_name="gemini-3-flash-preview",
        title="Title",
        created_at="2026-03-01T00:00:00",
        updated_at="2026-03-01T00:00:01",
        messages=[
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "world"},
        ],
        context=ChatContextHandle(cache_name="cached/123", cache_status="ready", cache_kind="pdf"),
    )
    service._sessions[session.session_id] = session
    service._latest_session_by_key[(session.article_id, session.model_key)] = session.session_id
    service._persist_state()

    restored = ChatService(
        settings=settings,
        storage_manager=storage,
        llm_processor=LLMProcessor(settings),
    )
    payload = restored.get_session(article_id="article-1", model_key="flash")

    assert payload["session_id"] == "chat-persist"
    assert payload["messages"][0]["text"] == "hello"
    assert payload["cache"]["kind"] == "pdf"


def test_chat_service_deletes_article_state(tmp_path) -> None:
    settings = Settings.from_env()
    settings.gemini_api_key = None
    settings.data_dir = tmp_path
    storage = StorageManager(tmp_path)
    service = ChatService(
        settings=settings,
        storage_manager=storage,
        llm_processor=LLMProcessor(settings),
    )

    session = ChatSession(
        session_id="chat-delete",
        article_id="article-delete",
        model_key="flash",
        model_name="gemini-3-flash-preview",
        title="Delete",
        created_at="2026-03-01T00:00:00",
        updated_at="2026-03-01T00:00:01",
        messages=[{"role": "user", "text": "hello"}],
        context=ChatContextHandle(cache_name="cached/123", cache_status="ready", cache_kind="pdf"),
    )
    service._sessions[session.session_id] = session
    service._latest_session_by_key[(session.article_id, session.model_key)] = session.session_id
    service._contexts[(session.article_id, session.model_name)] = session.context
    service._persist_state()

    result = service.delete_article_state("article-delete")

    assert result == {"sessions_removed": 1, "contexts_removed": 1}
    assert service.get_session(article_id="article-delete", model_key="flash")["session_id"] == ""
    restored = ChatService(
        settings=settings,
        storage_manager=storage,
        llm_processor=LLMProcessor(settings),
    )
    assert restored.get_session(article_id="article-delete", model_key="flash")["session_id"] == ""
