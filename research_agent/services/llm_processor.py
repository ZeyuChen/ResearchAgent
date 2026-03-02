from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types

from research_agent.config import Settings
from research_agent.models import ResearchItem, StoredItem


LOGGER = logging.getLogger(__name__)
GEMINI_3_FLASH_PREVIEW_INPUT_PER_1M = 0.50
GEMINI_3_FLASH_PREVIEW_OUTPUT_PER_1M = 3.00
GEMINI_3_FLASH_PRICING_URL = "https://ai.google.dev/pricing"
MODEL_PRICING = {
    "gemini-3-flash-preview": {
        "input_per_1m": GEMINI_3_FLASH_PREVIEW_INPUT_PER_1M,
        "output_per_1m": GEMINI_3_FLASH_PREVIEW_OUTPUT_PER_1M,
        "pricing_basis": "Google AI Studio Gemini 3 Flash Preview standard pricing (text/image/video input, text output)",
    },
}
MAX_WEB_SUMMARY_CHARS = 300
ProgressCallback = Callable[[int, str], None]


class LLMProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
        self.system_prompt = settings.load_gemini_prompt()

    @property
    def available(self) -> bool:
        return self.client is not None

    def summary_is_relevant(self, title: str, summary: str) -> bool:
        if not self.client:
            return True
        prompt = (
            "请你判断下面这篇内容是否值得纳入“大模型强化学习与基础设施研究库”。"
            "如果与 RLHF、偏好优化、强化学习、MoE、分布式训练、推理基础设施等相关，回答 YES；否则回答 NO。\n\n"
            f"标题：{title}\n摘要：{summary}"
        )
        response = self.client.models.generate_content(
            model=self.settings.secondary_filter_model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        answer = (response.text or "").strip().upper()
        return answer.startswith("YES")

    def generate_article(self, stored_item: StoredItem) -> str:
        article, _ = self.generate_article_with_metrics(stored_item)
        return article

    def translate_arxiv_summary(
        self,
        summary: str,
        title: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        source_summary = " ".join(summary.split())
        if not source_summary:
            return "", self._empty_usage()

        if not self.client:
            return source_summary, self._empty_usage()

        self._notify(progress_callback, 22, "Gemini 正在翻译 arXiv 原始摘要。")
        best_translation = ""
        total_usage = self._empty_usage()
        for attempt in (1, 2):
            try:
                response = self.client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=self._build_arxiv_translation_prompt(
                        source_summary=source_summary,
                        title=title,
                        retry=attempt > 1,
                    ),
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=2048,
                    ),
                )
                usage = self.extract_usage(response, self.settings.gemini_model)
                total_usage = self.merge_usage(total_usage, usage)
                translated = self._extract_translated_summary_text(response.text or "")
                if len(translated) > len(best_translation):
                    best_translation = translated
                if self._translation_looks_complete(source_summary, translated):
                    return translated, total_usage
                LOGGER.warning(
                    "Gemini arXiv summary translation looks incomplete for %s on attempt %s",
                    title or "unknown",
                    attempt,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Gemini arXiv summary translation failed for %s on attempt %s: %s",
                    title or "unknown",
                    attempt,
                    exc,
                )

        chunked_translation, chunked_usage = self._translate_arxiv_summary_in_chunks(source_summary, title)
        if chunked_translation:
            total_usage = self.merge_usage(total_usage, chunked_usage)
            if len(chunked_translation) > len(best_translation):
                best_translation = chunked_translation

        return (best_translation or source_summary), total_usage

    def summarize_article_markdown(
        self,
        article_markdown: str,
        item: ResearchItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        if not article_markdown.strip():
            return self._normalize_web_summary(item.summary or item.title), self._empty_usage()

        if not self.client:
            return self._fallback_article_summary(article_markdown, item), self._empty_usage()

        self._notify(progress_callback, 88, "Gemini 正在压缩生成知识库摘要。")
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=(
                    "下面是一篇基于原始网页或技术博客生成的深度解析文章。"
                    "请把它压缩成一个适合知识库列表展示的短摘要。"
                    "请以 JSON 返回，格式为 {\"summary\":\"...\"}，不要输出其他字段。"
                    "只输出最终摘要正文，不要解释规则，不要复述要求，不要出现“摘要：”字样。"
                    "摘要必须控制在 300 个中文字符以内，优先保留主题、关键技术点、重要结果与结论。\n\n"
                    f"标题：{item.title}\n"
                    f"来源：{item.source_url}\n\n"
                    f"解析正文：\n{article_markdown[:20000]}"
                ),
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=256,
                    response_mime_type="application/json",
                ),
            )
            usage = self.extract_usage(response, self.settings.gemini_model)
            summary = self._normalize_web_summary(self._extract_summary_text(response.text or ""))
            if not summary:
                summary = self._fallback_article_summary(article_markdown, item)
            return summary, usage
        except Exception as exc:
            LOGGER.warning("Gemini article summary generation failed for %s: %s", item.title, exc)
            return self._fallback_article_summary(article_markdown, item), self._empty_usage()

    def generate_topic_tags(
        self,
        seed_text: str,
        item: ResearchItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[str], dict]:
        seed = " ".join(seed_text.split())
        if not seed:
            return [], self._empty_usage()

        if not self.client:
            return self._fallback_topic_tags(seed), self._empty_usage()

        self._notify(progress_callback, 90, "Gemini 正在生成主题标签。")
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=(
                    "请根据下面内容提炼 3 到 6 个高价值主题标签。"
                    "标签用于后续检索和归档，请优先保留具体技术实体、方法名、模型名或研究主题。"
                    "例如：RL、Agent、VLM、MoE、Kimi、Verl、DPO。"
                    "请以 JSON 返回，格式为 {\"tags\":[\"RL\",\"Agent\"]}，不要输出其他内容。\n\n"
                    f"标题：{item.title}\n"
                    f"来源：{item.source_url}\n\n"
                    f"内容：\n{seed[:16000]}"
                ),
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=256,
                    response_mime_type="application/json",
                ),
            )
            usage = self.extract_usage(response, self.settings.gemini_model)
            tags = self._extract_topic_tags(response.text or "")
            if not tags:
                tags = self._fallback_topic_tags(seed)
            return tags, usage
        except Exception as exc:
            LOGGER.warning("Gemini topic tag generation failed for %s: %s", item.title, exc)
            return self._fallback_topic_tags(seed), self._empty_usage()

    def generate_article_with_metrics(
        self,
        stored_item: StoredItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        if not self.client:
            return self._fallback_article(stored_item.item), self._empty_usage()

        pdf_path = stored_item.source_files.get("source.pdf")
        if pdf_path and pdf_path.exists():
            try:
                return self._generate_from_pdf(pdf_path, stored_item.item, progress_callback=progress_callback)
            except Exception as exc:
                LOGGER.warning("Gemini PDF generation failed for %s: %s", stored_item.item.title, exc)

        html_path = stored_item.source_files.get("source.html")
        if html_path and html_path.exists():
            try:
                return self._generate_from_html(html_path, stored_item.item, progress_callback=progress_callback)
            except Exception as exc:
                LOGGER.warning("Gemini HTML generation failed for %s: %s", stored_item.item.title, exc)

        return self._fallback_article(stored_item.item), self._empty_usage()

    def _generate_from_pdf(
        self,
        pdf_path: Path,
        item: ResearchItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        self._notify(progress_callback, 52, "正在上传 PDF 到 Gemini File API。")
        uploaded_file = self._upload_file_with_retry(pdf_path, "PDF")
        try:
            return self._generate_from_pdf_chunked(
                uploaded_file=uploaded_file,
                item=item,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            LOGGER.warning("Gemini chunked PDF generation failed for %s: %s", item.title, exc)
            self._notify(progress_callback, 70, "分块翻译未成功，回退到单次全文生成。")

        return self._generate_from_pdf_single_pass(
            uploaded_file=uploaded_file,
            item=item,
            progress_callback=progress_callback,
        )

    def _generate_from_pdf_chunked(
        self,
        *,
        uploaded_file: types.File,
        item: ResearchItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        cached_content_name, cache_usage = self._create_pdf_context_cache(
            uploaded_file=uploaded_file,
            item=item,
            progress_callback=progress_callback,
        )
        total_usage = self.merge_usage(cache_usage)

        try:
            self._notify(progress_callback, 62, "Gemini 正在规划论文分块翻译结构。")
            plan_payload, plan_usage = self._request_pdf_translation_plan(
                uploaded_file=uploaded_file,
                cached_content_name=cached_content_name,
                item=item,
            )
            total_usage = self.merge_usage(total_usage, plan_usage)
            chunks = self._expand_pdf_translation_plan(self._normalize_pdf_translation_plan(plan_payload))
            if not chunks:
                raise ValueError("Gemini did not return a usable PDF translation plan.")

            translated_chunks: list[dict[str, object]] = []
            translatable_count = sum(1 for chunk in chunks if not self._chunk_should_skip_translation(chunk))
            completed = 0
            for index, chunk in enumerate(chunks, start=1):
                if self._chunk_should_skip_translation(chunk):
                    continue
                completed += 1
                progress = 64 + min(18, int(completed / max(translatable_count, 1) * 18))
                self._notify(
                    progress_callback,
                    progress,
                    f"Gemini 正在翻译分块 {completed}/{translatable_count}：{chunk['heading']}",
                )
                chunk_payload, chunk_usage = self._request_pdf_translation_chunk(
                    uploaded_file=uploaded_file,
                    cached_content_name=cached_content_name,
                    item=item,
                    chunk=chunk,
                    chunk_index=index,
                    total_chunks=len(chunks),
                )
                total_usage = self.merge_usage(total_usage, chunk_usage)
                normalized_chunk = self._normalize_pdf_translation_chunk(chunk_payload, fallback_chunk=chunk)
                if normalized_chunk["segments"]:
                    translated_chunks.append(normalized_chunk)

            if not translated_chunks:
                raise ValueError("Gemini did not return any translated PDF chunks.")

            stitched_body = self._stitch_chunked_pdf_sections(translated_chunks)

            self._notify(progress_callback, 84, "Gemini 正在生成核心摘要与关键图表解读。")
            summary_text, summary_usage = self._request_pdf_summary(
                uploaded_file=uploaded_file,
                cached_content_name=cached_content_name,
                item=item,
                translated_chunks=translated_chunks,
            )
            artifacts_text, artifacts_usage = self._request_pdf_key_artifacts(
                uploaded_file=uploaded_file,
                cached_content_name=cached_content_name,
                item=item,
                translated_chunks=translated_chunks,
            )
            commentary_text, commentary_usage = self._request_pdf_expert_commentary(
                item=item,
                translated_body=stitched_body,
            )
            total_usage = self.merge_usage(total_usage, summary_usage, artifacts_usage, commentary_usage)

            self._notify(progress_callback, 92, "正在整理多轮翻译结果。")
            article = self._stitch_chunked_pdf_article(
                item=item,
                summary_text=summary_text,
                translated_body=stitched_body,
                artifacts_text=artifacts_text,
                commentary_text=commentary_text,
            )
            return article, total_usage
        finally:
            if cached_content_name:
                try:
                    self.client.caches.delete(name=cached_content_name)
                except Exception as exc:
                    LOGGER.warning("Gemini cache cleanup failed for %s: %s", item.title, exc)

    def _generate_from_pdf_single_pass(
        self,
        *,
        uploaded_file: types.File,
        item: ResearchItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        self._notify(progress_callback, 72, "PDF 已上传，Gemini 正在深度阅读并生成解析。")
        response = self.client.models.generate_content(
            model=self.settings.gemini_model,
            contents=[
                (
                    "请深度阅读这份 PDF。主体请严格按原文章节顺序输出，并在每个章节下按段落顺序逐段翻译。"
                    "不要把高信息密度内容压成几句总结。"
                    "请尽量保留关键公式、图表、表格、实验设置、并列要点和附录中的关键技术细节。"
                    "请在内容顺序上体现段落顺序，但不要显式输出“段落1 / 段落2 / 段落3”这类机械标签。"
                    "全文转述完成后，再单独追加一节“专家点评”，从数据、算法、工程 / Infra 三个维度点评创新点与落地价值。"
                    "请尽量给出关键图表 / 表格 / 公式对应的页码标记，例如 [P12]。"
                ),
                uploaded_file,
            ],
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.4,
                max_output_tokens=8192,
            ),
        )
        usage = self.extract_usage(response, self.settings.gemini_model)
        return (response.text or "").strip() or self._fallback_article(item), usage

    def _create_pdf_context_cache(
        self,
        *,
        uploaded_file: types.File,
        item: ResearchItem,
        progress_callback: ProgressCallback | None,
    ) -> tuple[str | None, dict]:
        self._notify(progress_callback, 58, "正在创建 Gemini 论文上下文缓存。")
        try:
            cached = self.client.caches.create(
                model=self.settings.gemini_model,
                config=types.CreateCachedContentConfig(
                    display_name=f"ResearchAgent::{item.identifier[:48]}",
                    ttl="3600s",
                    contents=[uploaded_file],
                    system_instruction=self.system_prompt,
                ),
            )
            return getattr(cached, "name", None), self._extract_cached_usage(cached)
        except Exception as exc:
            LOGGER.warning("Gemini cache creation failed for %s: %s", item.title, exc)
            return None, self._empty_usage()

    def _request_pdf_translation_plan(
        self,
        *,
        uploaded_file: types.File,
        cached_content_name: str | None,
        item: ResearchItem,
    ) -> tuple[object, dict]:
        prompt = (
            "你现在先不要直接输出整篇译文，而是为后续的多轮逐段翻译制定一个稳定的翻译任务计划。"
            "请结合完整论文上下文，把正文按原文章节 / 小节顺序拆成多个可独立翻译的块。"
            "每个块必须足够小，使得在一次调用里可以稳定输出更多细节，而不会因为输出过长而退化成总结。"
            "优先按原文小节切分；如果某个小节很长，可以拆成 Part 1 / Part 2。"
            "不要把相邻的大章节粗暴合并成一个 chunk，例如不要把 Abstract 和 Introduction 合成一块。"
            "References / Bibliography / Acknowledgements 这类纯引用或低信息密度章节应当单独标记为 skip_translation=true。"
            "只返回 JSON 对象，不要输出 Markdown 或解释。\n\n"
            "JSON 格式：\n"
            "{\n"
            '  "chunks": [\n'
            "    {\n"
            '      "heading": "2 Method",\n'
            '      "page_refs": ["P4", "P5"],\n'
            '      "translation_scope": "说明这一块覆盖哪些段落 / 小点",\n'
            '      "skip_translation": false\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "硬性要求：\n"
            "- chunks 必须按原文顺序排列；\n"
            "- 尽量覆盖 Introduction、Method、Experiments、Appendix 中的关键技术内容；\n"
            "- 每个 chunk 都要给出尽量准确的页码参考；\n"
            "- 总 chunk 数控制在 8 到 20 个之间；\n"
            "- 每个 chunk 尽量只覆盖一个小节，最多覆盖一个紧密相关的小节组；\n"
            "- 不要遗漏关键附录；\n"
            "- 不要输出空 chunks。\n\n"
            f"论文标题：{item.title}"
        )
        response = self._generate_with_pdf_context(
            prompt=prompt,
            uploaded_file=uploaded_file,
            cached_content_name=cached_content_name,
            temperature=0.1,
            max_output_tokens=4096,
            response_mime_type="application/json",
            response_json_schema={
                "type": "object",
                "properties": {
                    "chunks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "page_refs": {"type": "array", "items": {"type": "string"}},
                                "translation_scope": {"type": "string"},
                                "skip_translation": {"type": "boolean"},
                            },
                            "required": ["heading"],
                        },
                    }
                },
                "required": ["chunks"],
            },
        )
        return self._extract_json_payload(response.text or ""), self.extract_usage(response, self.settings.gemini_model)

    def _request_pdf_translation_chunk(
        self,
        *,
        uploaded_file: types.File,
        cached_content_name: str | None,
        item: ResearchItem,
        chunk: dict[str, object],
        chunk_index: int,
        total_chunks: int,
    ) -> tuple[object, dict]:
        page_ref_text = " ".join(chunk.get("page_refs", [])) or "未提供"
        prompt = (
            "你现在执行多轮翻译任务中的其中一块。"
            "请结合完整论文上下文，只翻译当前指定的章节 / 小节范围，不要提前翻译到别的章节，不要跳段，不要总结化缩写。"
            "请尽量逐段保留信息，把内容拆成多个 segments，避免把多个高信息密度段落压成一段。\n\n"
            f"当前块序号：{chunk_index}/{total_chunks}\n"
            f"目标章节：{chunk['heading']}\n"
            f"页码参考：{page_ref_text}\n"
            f"翻译范围：{chunk.get('translation_scope', '')}\n\n"
            "只返回 JSON 对象，不要输出 Markdown 或额外说明。\n"
            "JSON 格式：\n"
            "{\n"
            '  "heading": "2 Method",\n'
            '  "page_refs": ["P4", "P5"],\n'
            '  "segments": [\n'
            "    {\n"
            '      "original": "英文原句或英文原段关键原文",\n'
            '      "translation": "对应的中文忠实翻译"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "要求：\n"
            "- segments 按原文出现顺序排列；\n"
            "- original 要保留对应的英文原句或英文原段核心文本，用于防止遗漏；\n"
            "- translation 要尽量完整保留关键定义、公式含义、实验设置、并列要点与限制条件；\n"
            "- 如果一个段落里有多个小点，请拆成多个 segments；\n"
            "- 如果某一部分是纯引用或参考文献，不要胡乱翻译，直接返回空 segments。\n\n"
            f"论文标题：{item.title}"
        )
        try:
            response = self._generate_with_pdf_context(
                prompt=prompt,
                uploaded_file=uploaded_file,
                cached_content_name=cached_content_name,
                temperature=0.0,
                max_output_tokens=3072,
                response_mime_type="application/json",
                response_json_schema={
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "page_refs": {"type": "array", "items": {"type": "string"}},
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "original": {"type": "string"},
                                    "translation": {"type": "string"},
                                },
                                "required": ["translation"],
                            },
                        },
                    },
                    "required": ["heading", "segments"],
                },
            )
            payload = self._extract_json_payload(response.text or "")
            normalized = self._normalize_pdf_translation_chunk(payload, fallback_chunk=chunk)
            if normalized["segments"]:
                return normalized, self.extract_usage(response, self.settings.gemini_model)
        except Exception as exc:
            LOGGER.warning("Gemini structured PDF chunk translation failed for %s [%s]: %s", item.title, chunk["heading"], exc)

        fallback_prompt = (
            "你现在执行一次窄范围的忠实翻译回退。"
            "请仅翻译当前指定章节 / 小节，按原文顺序自然展开，不要总结，不要跳段。"
            "只输出正文，不要输出 JSON，不要解释。\n\n"
            f"目标章节：{chunk['heading']}\n"
            f"页码参考：{page_ref_text}\n"
            f"翻译范围：{chunk.get('translation_scope', '')}\n\n"
            f"论文标题：{item.title}"
        )
        response = self._generate_with_pdf_context(
            prompt=fallback_prompt,
            uploaded_file=uploaded_file,
            cached_content_name=cached_content_name,
            temperature=0.0,
            max_output_tokens=2048,
        )
        payload = {
            "heading": chunk["heading"],
            "page_refs": chunk.get("page_refs", []),
            "segments": [
                {
                    "original": "",
                    "translation": (response.text or "").strip(),
                }
            ],
        }
        return payload, self.extract_usage(response, self.settings.gemini_model)

    def _request_pdf_summary(
        self,
        *,
        uploaded_file: types.File,
        cached_content_name: str | None,
        item: ResearchItem,
        translated_chunks: list[dict[str, object]],
    ) -> tuple[str, dict]:
        heading_summary = "；".join(str(chunk.get("heading", "")).strip() for chunk in translated_chunks[:12] if str(chunk.get("heading", "")).strip())
        prompt = (
            "请基于完整论文上下文，为这篇论文生成一个更像导读的“核心摘要”。"
            "要求：4 到 6 句话，尽量覆盖论文的主要目标、关键方法、重要结果和主线贡献。"
            "不要输出标题，不要输出列表，只输出最终摘要正文。\n\n"
            f"论文标题：{item.title}\n"
            f"已翻译章节：{heading_summary}"
        )
        response = self._generate_with_pdf_context(
            prompt=prompt,
            uploaded_file=uploaded_file,
            cached_content_name=cached_content_name,
            temperature=0.1,
            max_output_tokens=768,
        )
        return (response.text or "").strip(), self.extract_usage(response, self.settings.gemini_model)

    def _request_pdf_key_artifacts(
        self,
        *,
        uploaded_file: types.File,
        cached_content_name: str | None,
        item: ResearchItem,
        translated_chunks: list[dict[str, object]],
    ) -> tuple[str, dict]:
        heading_summary = "；".join(str(chunk.get("heading", "")).strip() for chunk in translated_chunks[:12] if str(chunk.get("heading", "")).strip())
        prompt = (
            "请基于完整论文上下文，单独提炼这篇论文里最关键的图表、公式和表格。"
            "请优先列出真正承载核心信息的图 / 表 / 公式，并说明它们证明了什么。"
            "如果有关键页码，请在句末追加 [P12] 形式的页码标记。"
            "请输出 Markdown 列表正文，不要输出章节标题。\n\n"
            f"论文标题：{item.title}\n"
            f"已翻译章节：{heading_summary}"
        )
        response = self._generate_with_pdf_context(
            prompt=prompt,
            uploaded_file=uploaded_file,
            cached_content_name=cached_content_name,
            temperature=0.1,
            max_output_tokens=1536,
        )
        return (response.text or "").strip(), self.extract_usage(response, self.settings.gemini_model)

    def _request_pdf_expert_commentary(
        self,
        *,
        item: ResearchItem,
        translated_body: str,
    ) -> tuple[str, dict]:
        if not self.client:
            return "", self._empty_usage()
        response = self.client.models.generate_content(
            model=self.settings.gemini_model,
            contents=(
                "下面是基于一篇论文所做的中文逐节忠实转写结果。"
                "请基于这份内容，单独输出一段更凝练但专业的“专家点评”。"
                "请从数据、算法、工程 / Infra 三个维度点评："
                "哪些点是实质创新，哪些更像工程整合，哪些落地价值高，哪些实现难度大。"
                "请输出 Markdown 列表正文，不要输出章节标题。\n\n"
                f"论文标题：{item.title}\n\n"
                f"逐节转写：\n{translated_body[:24000]}"
            ),
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1536,
            ),
        )
        return (response.text or "").strip(), self.extract_usage(response, self.settings.gemini_model)

    def _generate_with_pdf_context(
        self,
        *,
        prompt: str,
        uploaded_file: types.File,
        cached_content_name: str | None,
        temperature: float,
        max_output_tokens: int,
        response_mime_type: str | None = None,
        response_json_schema: dict[str, Any] | None = None,
    ) -> types.GenerateContentResponse:
        config_kwargs: dict[str, object] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type
        if response_json_schema:
            config_kwargs["response_json_schema"] = response_json_schema
        if cached_content_name:
            config_kwargs["cached_content"] = cached_content_name
            contents: object = prompt
        else:
            contents = [prompt, uploaded_file]
        last_exc: Exception | None = None
        token_limit = max_output_tokens
        for attempt in range(1, 4):
            try:
                config_kwargs["max_output_tokens"] = token_limit
                return self.client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= 3:
                    break
                token_limit = max(768, int(token_limit * 0.75))
                LOGGER.warning(
                    "Gemini PDF request failed on attempt %s; retrying with max_output_tokens=%s: %s",
                    attempt,
                    token_limit,
                    exc,
                )
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _chunk_should_skip_translation(chunk: dict[str, object]) -> bool:
        if bool(chunk.get("skip_translation")):
            return True
        heading = str(chunk.get("heading", "")).lower()
        return any(keyword in heading for keyword in ("reference", "bibliography", "acknowledgement", "acknowledgment"))

    @staticmethod
    def _normalize_pdf_translation_plan(payload: object) -> list[dict[str, object]]:
        if isinstance(payload, list):
            raw_chunks = payload
        elif isinstance(payload, dict):
            raw_chunks = payload.get("chunks", [])
        else:
            raw_chunks = []

        if not isinstance(raw_chunks, list):
            return []

        normalized: list[dict[str, object]] = []
        seen_keys: set[str] = set()
        for entry in raw_chunks[:18]:
            if not isinstance(entry, dict):
                continue
            heading = " ".join(str(entry.get("heading", "")).split()).strip()
            if not heading:
                continue
            translation_scope = " ".join(str(entry.get("translation_scope", "")).split()).strip()
            page_refs = LLMProcessor._normalize_page_refs(entry.get("page_refs"))
            key = f"{heading.lower()}|{' '.join(page_refs)}|{translation_scope.lower()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            normalized.append(
                {
                    "heading": heading,
                    "page_refs": page_refs,
                    "translation_scope": translation_scope or f"仅翻译 {heading} 相关内容。",
                    "skip_translation": bool(entry.get("skip_translation", False)),
                }
            )
        return normalized

    @staticmethod
    def _expand_pdf_translation_plan(chunks: list[dict[str, object]]) -> list[dict[str, object]]:
        expanded: list[dict[str, object]] = []
        for chunk in chunks:
            subdivisions = LLMProcessor._split_coarse_chunk(chunk)
            if subdivisions:
                expanded.extend(subdivisions)
            else:
                expanded.append(chunk)
        return expanded[:24]

    @staticmethod
    def _split_coarse_chunk(chunk: dict[str, object]) -> list[dict[str, object]]:
        heading = str(chunk.get("heading", "")).strip()
        if not heading:
            return []
        if bool(chunk.get("skip_translation")):
            return []

        numbered_parts = re.findall(r"\d+(?:\.\d+)*\s+[^&]+?(?=(?:\s+&\s+\d+(?:\.\d+)*\s+)|$)", heading)
        if numbered_parts and len(numbered_parts) >= 2:
            parts = [part.strip(" ;,") for part in numbered_parts]
        elif " & " in heading:
            parts = [part.strip() for part in heading.split(" & ") if part.strip()]
            if len(parts) < 2:
                return []
        else:
            return []

        translation_scope = str(chunk.get("translation_scope", "")).strip()
        page_refs = list(chunk.get("page_refs", []))
        split_chunks: list[dict[str, object]] = []
        for part in parts:
            split_chunks.append(
                {
                    "heading": part,
                    "page_refs": page_refs,
                    "translation_scope": (
                        f"仅翻译 {part} 对应的段落、小点和图表，不要覆盖同一大块里的其他部分。"
                        if not translation_scope
                        else f"{translation_scope} 当前仅处理其中的 {part}。"
                    ),
                    "skip_translation": False,
                }
            )
        return split_chunks

    @staticmethod
    def _normalize_pdf_translation_chunk(
        payload: object,
        *,
        fallback_chunk: dict[str, object],
    ) -> dict[str, object]:
        heading = str(fallback_chunk.get("heading", "")).strip()
        page_refs = LLMProcessor._normalize_page_refs(fallback_chunk.get("page_refs"))
        if not isinstance(payload, dict):
            return {
                "heading": heading,
                "page_refs": page_refs,
                "segments": [],
            }

        payload_heading = " ".join(str(payload.get("heading", heading)).split()).strip()
        if payload_heading:
            heading = payload_heading
        payload_page_refs = LLMProcessor._normalize_page_refs(payload.get("page_refs"))
        if payload_page_refs:
            page_refs = payload_page_refs

        raw_segments = payload.get("segments", [])
        segments: list[dict[str, str]] = []
        if isinstance(raw_segments, list):
            for entry in raw_segments:
                if not isinstance(entry, dict):
                    continue
                original = " ".join(str(entry.get("original", "")).split()).strip()
                translation = str(entry.get("translation", "")).strip()
                if not translation:
                    continue
                segments.append(
                    {
                        "original": original,
                        "translation": translation,
                    }
                )

        return {
            "heading": heading,
            "page_refs": page_refs,
            "segments": segments,
        }

    @staticmethod
    def _normalize_page_refs(value: object) -> list[str]:
        refs: list[str] = []
        if isinstance(value, list):
            candidates = value
        elif isinstance(value, tuple):
            candidates = list(value)
        elif value is None:
            candidates = []
        else:
            candidates = [value]

        for candidate in candidates:
            text = str(candidate).upper()
            for match in re.findall(r"P\d{1,4}", text):
                if match not in refs:
                    refs.append(match)
        return refs

    @staticmethod
    def _extract_json_payload(raw_text: str) -> object:
        payload = raw_text.strip()
        if not payload:
            return None
        payload = payload.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        decoded = LLMProcessor._decode_summary_payload(payload)
        if decoded is not None:
            return decoded

        for opener, closer in (("{", "}"), ("[", "]")):
            start = payload.find(opener)
            end = payload.rfind(closer)
            if start != -1 and end > start:
                decoded = LLMProcessor._decode_summary_payload(payload[start : end + 1])
                if decoded is not None:
                    return decoded
        return None

    @staticmethod
    def _stitch_chunked_pdf_sections(translated_chunks: list[dict[str, object]]) -> str:
        sections: list[str] = []
        for chunk in translated_chunks:
            heading = str(chunk.get("heading", "")).strip()
            if heading:
                sections.append(f"## {heading}")
            page_refs = [ref for ref in chunk.get("page_refs", []) if str(ref).strip()]
            if page_refs:
                sections.append(f"_页码参考：{' '.join(str(ref) for ref in page_refs)}_")
            for segment in chunk.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                translation = str(segment.get("translation", "")).strip()
                if translation:
                    sections.append(translation)
            sections.append("")
        return "\n\n".join(block for block in sections if block is not None).strip()

    @staticmethod
    def _stitch_chunked_pdf_article(
        *,
        item: ResearchItem,
        summary_text: str,
        translated_body: str,
        artifacts_text: str,
        commentary_text: str,
    ) -> str:
        sections = [f"# {item.title}"]
        if summary_text.strip():
            sections.extend(["## 核心摘要", summary_text.strip()])
        if translated_body.strip():
            sections.append(translated_body.strip())
        if artifacts_text.strip():
            sections.extend(["## 关键图表 / 公式 / 表格解读", artifacts_text.strip()])
        if commentary_text.strip():
            sections.extend(["## 专家点评：数据 / 算法 / 工程创新", commentary_text.strip()])
        return "\n\n".join(section for section in sections if section.strip())

    def _extract_cached_usage(self, cached_content: types.CachedContent) -> dict:
        usage = getattr(cached_content, "usage_metadata", None)
        if not usage:
            return self._empty_usage()
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        if not prompt_tokens:
            prompt_tokens = int(getattr(usage, "total_token_count", 0) or 0)
        if not prompt_tokens:
            return self._empty_usage()
        pricing = MODEL_PRICING.get(self.settings.gemini_model, {})
        input_per_1m = float(pricing.get("input_per_1m", 0.0) or 0.0)
        input_cost = prompt_tokens / 1_000_000 * input_per_1m
        return {
            "model": self.settings.gemini_model,
            "prompt_tokens": prompt_tokens,
            "output_tokens": 0,
            "total_tokens": prompt_tokens,
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": 0.0,
            "estimated_cost_usd": round(input_cost, 6),
            "pricing_basis": str(pricing.get("pricing_basis", "")),
            "pricing_reference_url": GEMINI_3_FLASH_PRICING_URL,
        }

    def _upload_file_with_retry(self, path: Path, label: str) -> types.File:
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                return self.client.files.upload(file=str(path))
            except Exception as exc:
                last_exc = exc
                if attempt >= 3:
                    break
                LOGGER.warning(
                    "Gemini File API upload failed for %s %s on attempt %s, retrying: %s",
                    label,
                    path.name,
                    attempt,
                    exc,
                )
        assert last_exc is not None
        raise last_exc

    def _generate_from_html(
        self,
        html_path: Path,
        item: ResearchItem,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict]:
        content_parts = self._build_webpage_content_parts(
            html_path=html_path,
            item=item,
            instruction=(
                "下面是技术报告、博客或网页的渲染后内容。"
                "请基于网页文字、截图和图片素材，输出一篇高质量 Markdown 技术解析文章。"
                "需要兼顾数据、算法、工程三条主线，并对工程 / Infra 技术点逐条拆解。"
            ),
            text_limit=50000,
            html_limit=40000,
            progress_callback=progress_callback,
            upload_progress=60,
        )

        self._notify(progress_callback, 74, "网页内容已就绪，Gemini 正在生成详细解读。")
        response = self.client.models.generate_content(
            model=self.settings.gemini_model,
            contents=content_parts,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.4,
                max_output_tokens=8192,
            ),
        )
        usage = self.extract_usage(response, self.settings.gemini_model)
        return (response.text or "").strip() or self._fallback_article(item), usage

    def _build_webpage_content_parts(
        self,
        html_path: Path,
        item: ResearchItem,
        instruction: str,
        text_limit: int,
        html_limit: int,
        progress_callback: ProgressCallback | None,
        upload_progress: int,
    ) -> list[object]:
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
        text_path = html_path.with_name("source.txt")
        text_excerpt = ""
        if text_path.exists():
            text_excerpt = text_path.read_text(encoding="utf-8", errors="ignore")[:text_limit]

        content_parts: list[object] = [
            (
                f"{instruction}\n\n"
                f"标题：{item.title}\n"
                f"来源：{item.source_url}\n"
                f"文本抽取：\n{text_excerpt}\n\n"
                f"HTML 片段：\n{html_text[:html_limit]}"
            )
        ]
        for image_path in self._collect_visual_assets(html_path.parent):
            self._notify(progress_callback, upload_progress, "正在上传网页截图与图片素材到 Gemini。")
            content_parts.append(self._upload_file_with_retry(image_path, "网页素材"))
        return content_parts

    @staticmethod
    def _collect_visual_assets(item_dir: Path) -> list[Path]:
        visual_assets: list[Path] = []
        for path in sorted(item_dir.iterdir()):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                continue
            visual_assets.append(path)
            if len(visual_assets) >= 4:
                break
        return visual_assets

    @staticmethod
    def _fallback_article(item: ResearchItem) -> str:
        tags = ", ".join(item.tags) if item.tags else "未标注"
        authors = ", ".join(item.authors) if item.authors else "未知"
        return f"""# {item.title}

## 核心摘要

当前未配置 Gemini API，以下内容为基于元数据的保底摘要。该条目来自 **{item.source}**，适合作为后续人工复核与二次阅读候选。

## 背景痛点

围绕大模型强化学习与基础设施演进，研究内容通常分散在论文、开源发布说明和社区论文导航中，难以形成统一知识沉淀。

## 核心架构 / 算法解析

- 标题：{item.title}
- 来源：{item.source_url}
- 作者：{authors}
- 标签：{tags}
- 发布时间：{item.published_at}

## 工程实现亮点

- 已完成本地元数据归档。
- 如存在 PDF / HTML 源文件，可继续通过 Gemini File API 进行深度阅读。

## 个人评价

该条目已通过关键词初筛，建议在配置 `GEMINI_API_KEY` 后执行完整的全文解读流程，以获得更高质量的专家分析。
"""

    @classmethod
    def merge_usage(cls, *usage_entries: dict) -> dict:
        valid_entries = [entry for entry in usage_entries if entry]
        if not valid_entries:
            return cls._empty_usage()

        merged = cls._empty_usage()
        merged["model"] = next((entry.get("model", "") for entry in reversed(valid_entries) if entry.get("model")), "")
        merged["pricing_basis"] = next(
            (entry.get("pricing_basis", "") for entry in reversed(valid_entries) if entry.get("pricing_basis")),
            "",
        )
        for key in ("prompt_tokens", "output_tokens", "total_tokens", "input_cost_usd", "output_cost_usd", "estimated_cost_usd"):
            merged[key] = round(sum(float(entry.get(key, 0) or 0) for entry in valid_entries), 6)
            if key.endswith("tokens"):
                merged[key] = int(merged[key])
        return merged

    @staticmethod
    @staticmethod
    def _fallback_article_summary(article_markdown: str, item: ResearchItem) -> str:
        for block in article_markdown.split("\n\n"):
            candidate = " ".join(line.strip() for line in block.splitlines() if line.strip())
            if not candidate:
                continue
            if candidate.startswith("#") or candidate.startswith("```"):
                continue
            if len(candidate) < 24:
                continue
            return LLMProcessor._normalize_web_summary(candidate)
        return LLMProcessor._normalize_web_summary(item.summary or item.title)

    @staticmethod
    def _normalize_web_summary(text: str) -> str:
        normalized = " ".join(text.split())
        while normalized.startswith(("#", "-", "*", " ")):
            normalized = normalized[1:].lstrip()
        for prefix in ("摘要：", "摘要:", "summary:", "Summary:"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].lstrip()
        if len(normalized) <= MAX_WEB_SUMMARY_CHARS:
            return normalized
        clipped = normalized[:MAX_WEB_SUMMARY_CHARS].rstrip("，。；：,.;: ")
        return f"{clipped}..."

    @staticmethod
    def _extract_summary_text(raw_text: str) -> str:
        payload = raw_text.strip()
        if not payload:
            return ""
        decoded = LLMProcessor._decode_summary_payload(payload)
        if decoded is None:
            start = payload.find("{")
            end = payload.rfind("}")
            if start != -1 and end > start:
                decoded = LLMProcessor._decode_summary_payload(payload[start : end + 1])
        if decoded is None:
            lowered = payload.lower()
            if "json requested" in lowered or "```" in lowered or "{" in payload or "}" in payload:
                return ""
            return payload
        if isinstance(decoded, dict):
            return str(decoded.get("summary", "")).strip()
        return payload

    @staticmethod
    def _clean_translated_summary_text(raw_text: str) -> str:
        payload = raw_text.strip()
        if not payload:
            return ""
        payload = payload.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        payload = payload.replace("{ \"translation\":", "").replace('{"translation":', "").strip()
        payload = payload.removeprefix('"').removeprefix("'").strip()
        payload = payload.removesuffix("}").removesuffix('"').removesuffix("'").strip()
        cleaned_lines: list[str] = []
        for raw_line in payload.splitlines():
            line = raw_line.strip().lstrip("*- ").strip()
            lowered = line.lower()
            if not line:
                continue
            if any(
                lowered.startswith(prefix)
                for prefix in (
                    "\"translation\":",
                    "translation\" :",
                    "{ \"translation\":",
                    "{\"translation\":",
                    "natural/accurate",
                    "faithful",
                    "no expansion",
                    "no deletion",
                    "no evaluation",
                    "translation:",
                    "中文译文:",
                    "中文译文：",
                )
            ):
                continue
            cleaned_lines.append(line)
        return " ".join(" ".join(cleaned_lines).split()).strip()

    @staticmethod
    def _build_arxiv_translation_prompt(*, source_summary: str, title: str, retry: bool) -> str:
        retry_clause = (
            "你上一次的输出可能没有翻译完整，这一次请从头完整翻译，不要中途截断。"
            if retry
            else ""
        )
        return (
            "请把下面这段 arXiv 论文摘要完整翻译成自然、准确的中文。"
            "要求：只做忠实翻译，保持原意，不扩写，不删减，不添加评价，不解释术语。"
            "请保留原文中的列表编号、括号和关键技术名词。"
            "只输出最终中文译文正文，不要输出 JSON、代码块、说明文字或“摘要：”前缀。"
            f"{retry_clause}\n\n"
            f"论文标题：{title}\n\n"
            f"英文摘要：\n{source_summary}"
        )

    @staticmethod
    def _extract_translated_summary_text(raw_text: str) -> str:
        payload = raw_text.strip()
        if not payload:
            return ""
        decoded = LLMProcessor._decode_summary_payload(payload)
        if decoded is None:
            start = payload.find("{")
            end = payload.rfind("}")
            if start != -1 and end > start:
                decoded = LLMProcessor._decode_summary_payload(payload[start : end + 1])
        if isinstance(decoded, dict):
            candidate = str(decoded.get("translation", "")).strip()
            if candidate:
                return LLMProcessor._clean_translated_summary_text(candidate)
        regex_match = re.search(r'"translation"\s*:\s*"(.*)', payload, re.DOTALL)
        if regex_match:
            candidate = regex_match.group(1).strip()
            return LLMProcessor._clean_translated_summary_text(candidate)
        return LLMProcessor._clean_translated_summary_text(payload)

    @staticmethod
    def _translation_looks_complete(source_summary: str, translated: str) -> bool:
        text = " ".join(str(translated or "").split()).strip()
        if not text:
            return False
        source_words = len(" ".join(source_summary.split()).split())
        min_chars = max(80, min(320, int(source_words * 1.35)))
        if len(text) < min_chars:
            return False
        if source_words >= 80 and text[-1] not in "。！？.!?）)]】":
            return False
        return True

    def _translate_arxiv_summary_in_chunks(self, source_summary: str, title: str) -> tuple[str, dict]:
        if not self.client:
            return "", self._empty_usage()

        chunks = self._split_english_summary_chunks(source_summary)
        if len(chunks) <= 1:
            return "", self._empty_usage()

        translated_chunks: list[str] = []
        total_usage = self._empty_usage()
        for chunk in chunks:
            try:
                response = self.client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=(
                        "下面是一段 arXiv 论文摘要片段。"
                        "请将它忠实、完整地翻译成中文。"
                        "要求：只翻译，不扩写，不删减，不解释。"
                        "保留括号、编号和关键技术名词。"
                        "只输出最终中文译文正文。\n\n"
                        f"论文标题：{title}\n\n"
                        f"英文摘要片段：\n{chunk}"
                    ),
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=1024,
                    ),
                )
                total_usage = self.merge_usage(total_usage, self.extract_usage(response, self.settings.gemini_model))
                translated = self._extract_translated_summary_text(response.text or "")
                if translated:
                    translated_chunks.append(translated)
            except Exception as exc:
                LOGGER.warning("Gemini chunked arXiv summary translation failed for %s: %s", title or "unknown", exc)

        merged = " ".join(chunk.strip() for chunk in translated_chunks if chunk.strip()).strip()
        return merged, total_usage

    @staticmethod
    def _split_english_summary_chunks(source_summary: str, max_words: int = 55) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(source_summary.split()).strip())
        chunks: list[str] = []
        current: list[str] = []
        current_words = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            word_count = len(sentence.split())
            if current and current_words + word_count > max_words:
                chunks.append(" ".join(current).strip())
                current = [sentence]
                current_words = word_count
                continue
            current.append(sentence)
            current_words += word_count
        if current:
            chunks.append(" ".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _decode_summary_payload(payload: str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_topic_tags(raw_text: str) -> list[str]:
        payload = raw_text.strip()
        if not payload:
            return []
        decoded = LLMProcessor._decode_summary_payload(payload)
        if decoded is None:
            start = payload.find("{")
            end = payload.rfind("}")
            if start != -1 and end > start:
                decoded = LLMProcessor._decode_summary_payload(payload[start : end + 1])
        if not isinstance(decoded, dict):
            return []
        tags = decoded.get("tags", [])
        if not isinstance(tags, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in tags:
            tag = str(raw_tag).strip().lstrip("#")
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(tag[:24])
            if len(normalized) >= 6:
                break
        return normalized

    @staticmethod
    def _fallback_topic_tags(seed_text: str) -> list[str]:
        candidates = ("RL", "Agent", "RLHF", "PPO", "DPO", "GRPO", "MoE", "VLM", "Kimi", "Verl", "Forge", "MiniMax")
        lowered = seed_text.lower()
        tags: list[str] = []
        for candidate in candidates:
            pattern = rf"(?<![a-z0-9]){re.escape(candidate.lower())}(?![a-z0-9])"
            if re.search(pattern, lowered):
                tags.append(candidate)
        return tags[:6]

    def extract_usage(self, response: types.GenerateContentResponse, model_name: str) -> dict:
        usage = response.usage_metadata
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        total_tokens = int(getattr(usage, "total_token_count", prompt_tokens + output_tokens) or 0)
        pricing = MODEL_PRICING.get(model_name, {})
        input_per_1m = float(pricing.get("input_per_1m", 0.0) or 0.0)
        output_per_1m = float(pricing.get("output_per_1m", 0.0) or 0.0)
        input_cost = prompt_tokens / 1_000_000 * input_per_1m
        output_cost = output_tokens / 1_000_000 * output_per_1m
        return {
            "model": model_name,
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "estimated_cost_usd": round(input_cost + output_cost, 6),
            "pricing_basis": str(pricing.get("pricing_basis", "")),
            "pricing_reference_url": GEMINI_3_FLASH_PRICING_URL,
        }

    def _extract_usage(self, response: types.GenerateContentResponse) -> dict:
        return self.extract_usage(response, self.settings.gemini_model)

    @staticmethod
    def _empty_usage() -> dict:
        return {
            "model": "",
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "estimated_cost_usd": 0.0,
            "pricing_basis": "",
            "pricing_reference_url": GEMINI_3_FLASH_PRICING_URL,
        }

    @staticmethod
    def _notify(progress_callback: ProgressCallback | None, progress: int, message: str) -> None:
        if progress_callback:
            progress_callback(progress, message)
