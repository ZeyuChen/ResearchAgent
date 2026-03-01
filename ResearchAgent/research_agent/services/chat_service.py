from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from google.genai import types

from research_agent.config import Settings
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.storage_manager import StorageManager


LOGGER = logging.getLogger(__name__)
CHAT_HISTORY_WINDOW = 12
MARKDOWN_CACHE_MIN_CHARS = {
    "gemini-3-flash-preview": 1200,
    "gemini-3.1-pro-preview": 4000,
}


@dataclass(slots=True)
class ChatContextHandle:
    cache_name: str = ""
    cache_kind: str = "inline"
    cache_status: str = "inline"
    file_handle: object | None = None


@dataclass(slots=True)
class ChatSession:
    session_id: str
    article_id: str
    model_key: str
    model_name: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    context: ChatContextHandle = field(default_factory=ChatContextHandle)


class ChatService:
    def __init__(
        self,
        settings: Settings,
        storage_manager: StorageManager,
        llm_processor: LLMProcessor,
    ) -> None:
        self.settings = settings
        self.storage_manager = storage_manager
        self.llm_processor = llm_processor
        self._lock = threading.Lock()
        self._sessions: dict[str, ChatSession] = {}
        self._contexts: dict[tuple[str, str], ChatContextHandle] = {}

    def available(self) -> bool:
        return self.llm_processor.available

    def model_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "flash",
                "label": "Gemini 3 Flash Preview",
                "api_name": self.settings.chat_default_model,
                "recommended": True,
                "description": "默认模式，适合快速追问、原文定位与多轮技术讨论。",
            },
            {
                "key": "pro",
                "label": "Gemini 3.1 Pro Preview",
                "api_name": self.settings.chat_advanced_model,
                "recommended": False,
                "description": "更适合复杂追问与更长推理链路。Google 已要求从 Gemini 3 Pro Preview 迁移。",
            },
        ]

    def default_model_key(self) -> str:
        return "flash"

    def send_message(
        self,
        *,
        article: dict,
        article_id: str,
        message: str,
        model_key: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        if not self.available():
            raise RuntimeError("Gemini API is not configured")

        clean_message = " ".join(message.split())
        if not clean_message:
            raise ValueError("Message cannot be empty")

        model_name = self._resolve_model_name(model_key)
        session = self._get_or_create_session(
            article_id=article_id,
            title=article.get("title") or "Untitled",
            model_key=model_key,
            model_name=model_name,
            session_id=session_id,
        )
        context = self._ensure_context(article=article, model_name=model_name)
        if session.context.cache_name != context.cache_name or session.context.cache_status != context.cache_status:
            session.context = context

        prompt = self._build_turn_prompt(
            article=article,
            message=clean_message,
            history=session.messages,
            cache_kind=context.cache_kind,
            include_inline_context=context.cache_status == "inline",
        )
        response = self._generate_chat_response(prompt=prompt, context=context, model_name=model_name)
        reply = (response.text or "").strip() or "我没有拿到有效回复，请换一个问法再试一次。"
        usage = self.llm_processor.extract_usage(response, model_name)

        now = datetime.now().isoformat(timespec="seconds")
        session.messages.append({"role": "user", "text": clean_message, "created_at": now})
        session.messages.append({"role": "assistant", "text": reply, "created_at": now, "usage": usage})
        session.updated_at = now

        return self._serialize_session(session)

    def _get_or_create_session(
        self,
        *,
        article_id: str,
        title: str,
        model_key: str,
        model_name: str,
        session_id: str | None,
    ) -> ChatSession:
        with self._lock:
            if session_id:
                existing = self._sessions.get(session_id)
                if existing and existing.article_id == article_id and existing.model_name == model_name:
                    return existing

            now = datetime.now().isoformat(timespec="seconds")
            new_session = ChatSession(
                session_id=f"chat-{uuid.uuid4().hex[:12]}",
                article_id=article_id,
                model_key=model_key,
                model_name=model_name,
                title=title,
                created_at=now,
                updated_at=now,
            )
            self._sessions[new_session.session_id] = new_session
            return new_session

    def _ensure_context(self, *, article: dict, model_name: str) -> ChatContextHandle:
        cache_key = (article.get("article_id", ""), model_name)
        with self._lock:
            existing = self._contexts.get(cache_key)
            if existing:
                return existing

        context = self._build_context(article=article, model_name=model_name)
        with self._lock:
            self._contexts[cache_key] = context
        return context

    def _build_context(self, *, article: dict, model_name: str) -> ChatContextHandle:
        if not self.llm_processor.client:
            return ChatContextHandle()

        article_dir = self._resolve_article_dir(article)
        if article_dir is None:
            return ChatContextHandle()

        pdf_path = article_dir / "source.pdf"
        if pdf_path.exists():
            return self._build_pdf_context(article=article, model_name=model_name, pdf_path=pdf_path)

        markdown = str(article.get("markdown", "") or "")
        min_chars = MARKDOWN_CACHE_MIN_CHARS.get(model_name, 1800)
        if len(markdown) >= min_chars:
            return self._build_markdown_context(article=article, model_name=model_name, markdown=markdown)

        return ChatContextHandle(cache_kind="inline", cache_status="inline")

    def _build_pdf_context(self, *, article: dict, model_name: str, pdf_path: Path) -> ChatContextHandle:
        if not self.llm_processor.client:
            return ChatContextHandle()

        try:
            file_handle = self.llm_processor.client.files.upload(file=str(pdf_path))
            try:
                cached = self.llm_processor.client.caches.create(
                    model=model_name,
                    config=types.CreateCachedContentConfig(
                        display_name=f"ResearchAgent {article.get('article_id', 'paper')}",
                        ttl=self.settings.chat_cache_ttl,
                        system_instruction=self._chat_system_instruction(article, mode="pdf"),
                        contents=[file_handle],
                    ),
                )
                return ChatContextHandle(
                    cache_name=getattr(cached, "name", "") or "",
                    cache_kind="pdf",
                    cache_status="ready",
                    file_handle=file_handle,
                )
            except Exception as exc:
                LOGGER.warning("Gemini cached context creation failed for PDF %s: %s", article.get("article_id"), exc)
                return ChatContextHandle(cache_kind="pdf", cache_status="uploaded-file", file_handle=file_handle)
        except Exception as exc:
            LOGGER.warning("Gemini PDF upload failed for chat context %s: %s", article.get("article_id"), exc)
            return ChatContextHandle(cache_kind="inline", cache_status="inline")

    def _build_markdown_context(self, *, article: dict, model_name: str, markdown: str) -> ChatContextHandle:
        if not self.llm_processor.client:
            return ChatContextHandle()

        try:
            cached = self.llm_processor.client.caches.create(
                model=model_name,
                config=types.CreateCachedContentConfig(
                    display_name=f"ResearchAgent {article.get('article_id', 'article')}",
                    ttl=self.settings.chat_cache_ttl,
                    system_instruction=self._chat_system_instruction(article, mode="markdown"),
                    contents=[markdown[:50000]],
                ),
            )
            return ChatContextHandle(
                cache_name=getattr(cached, "name", "") or "",
                cache_kind="article",
                cache_status="ready",
            )
        except Exception as exc:
            LOGGER.warning("Gemini markdown cache creation failed for %s: %s", article.get("article_id"), exc)
            return ChatContextHandle(cache_kind="inline", cache_status="inline")

    def _generate_chat_response(
        self,
        *,
        prompt: str,
        context: ChatContextHandle,
        model_name: str,
    ):
        if not self.llm_processor.client:
            raise RuntimeError("Gemini API is not configured")

        config = types.GenerateContentConfig(
            temperature=0.25,
            max_output_tokens=4096,
        )
        if context.cache_name:
            config.cached_content = context.cache_name
            contents: list[object] = [prompt]
        elif context.file_handle is not None:
            contents = [prompt, context.file_handle]
        else:
            contents = [prompt]

        return self.llm_processor.client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

    def _build_turn_prompt(
        self,
        *,
        article: dict,
        message: str,
        history: list[dict[str, Any]],
        cache_kind: str,
        include_inline_context: bool,
    ) -> str:
        recent_history = history[-CHAT_HISTORY_WINDOW:]
        conversation = []
        for entry in recent_history:
            speaker = "用户" if entry.get("role") == "user" else "助手"
            conversation.append(f"{speaker}：{entry.get('text', '')}")
        conversation_text = "\n".join(conversation) or "（无）"

        prompt_parts = [
            "请基于已缓存的论文或解析上下文继续回答用户问题。",
            "回答时优先给出结构化要点，尽量覆盖数据、算法、工程三个层面。",
            "如果引用的是原文直接证据，请尽量标出页码；如果没有直接证据，请明确说明是基于上下文的推断。",
            f"当前文章：{article.get('title', 'Untitled')}",
            f"已知摘要：{article.get('summary', '')}",
            f"上下文模式：{cache_kind}",
            f"历史对话：\n{conversation_text}",
        ]
        if include_inline_context:
            prompt_parts.append(f"解析正文：\n{str(article.get('markdown', '') or '')[:24000]}")
        prompt_parts.append(f"本轮问题：{message}")
        return "\n\n".join(prompt_parts)

    @staticmethod
    def _chat_system_instruction(article: dict, mode: str) -> str:
        return (
            "你是 ResearchAgent 的论文深读助手。"
            "你会结合原始论文 PDF、技术解读文章或网页归档内容，帮助用户做追问式深度阅读。"
            "回答时不要泛泛而谈，要尽量逐条拆解关键技术点。"
            "默认关注数据、算法、工程三位一体，同时在 Infra 相关问题上展开到实现细节、性能瓶颈与工程取舍。"
            "必要时可以用少量形象类比帮助理解，但不能牺牲准确性。"
            f"当前模式：{mode}。当前文章：{article.get('title', 'Untitled')}。"
        )

    def _serialize_session(self, session: ChatSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "article_id": session.article_id,
            "title": session.title,
            "model_key": session.model_key,
            "model_name": session.model_name,
            "cache": {
                "status": session.context.cache_status,
                "kind": session.context.cache_kind,
            },
            "messages": list(session.messages),
            "updated_at": session.updated_at,
        }

    def _resolve_model_name(self, model_key: str) -> str:
        if model_key == "pro":
            return self.settings.chat_advanced_model
        return self.settings.chat_default_model

    def _resolve_article_dir(self, article: dict) -> Path | None:
        article_path = article.get("article_path", "")
        if not article_path:
            return None
        return (self.storage_manager.data_dir / article_path).parent
