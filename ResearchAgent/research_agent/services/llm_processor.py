from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable

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
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=(
                    "请把下面这段 arXiv 论文摘要翻译成自然、准确的中文。"
                    "要求：只做忠实翻译，保持原意，不扩写，不删减，不添加评价，不解释术语。"
                    "只输出最终中文译文本身。"
                    "不要输出 JSON，不要写校验说明，不要分点，不要加“摘要：”前缀。\n\n"
                    f"论文标题：{title}\n\n"
                    f"英文摘要：\n{source_summary}"
                ),
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=1024,
                ),
            )
            usage = self.extract_usage(response, self.settings.gemini_model)
            translated = self._clean_translated_summary_text(response.text or "")
            return (translated or source_summary), usage
        except Exception as exc:
            LOGGER.warning("Gemini arXiv summary translation failed for %s: %s", title or "unknown", exc)
            return source_summary, self._empty_usage()

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
        uploaded_file = self.client.files.upload(file=str(pdf_path))
        self._notify(progress_callback, 72, "PDF 已上传，Gemini 正在深度阅读并生成解析。")
        response = self.client.models.generate_content(
            model=self.settings.gemini_model,
            contents=[
                "请深度阅读这份 PDF。输出更完整、更详细的 Markdown 解析文章，务必覆盖数据、算法、工程三条主线，并且对工程 / Infra 技术点用编号列表逐条拆解。请尽量给出关键图表 / 表格 / 公式对应的页码标记，例如 [P12]。",
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
            content_parts.append(self.client.files.upload(file=str(image_path)))
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
