from __future__ import annotations

import logging
from pathlib import Path

from google import genai
from google.genai import types

from research_agent.config import Settings
from research_agent.models import ResearchItem, StoredItem


LOGGER = logging.getLogger(__name__)


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
        if not self.client:
            return self._fallback_article(stored_item.item)

        pdf_path = stored_item.source_files.get("source.pdf")
        if pdf_path and pdf_path.exists():
            try:
                return self._generate_from_pdf(pdf_path, stored_item.item)
            except Exception as exc:
                LOGGER.warning("Gemini PDF generation failed for %s: %s", stored_item.item.title, exc)

        html_path = stored_item.source_files.get("source.html")
        if html_path and html_path.exists():
            try:
                return self._generate_from_html(html_path, stored_item.item)
            except Exception as exc:
                LOGGER.warning("Gemini HTML generation failed for %s: %s", stored_item.item.title, exc)

        return self._fallback_article(stored_item.item)

    def _generate_from_pdf(self, pdf_path: Path, item: ResearchItem) -> str:
        uploaded_file = self.client.files.upload(file=str(pdf_path))
        response = self.client.models.generate_content(
            model=self.settings.gemini_model,
            contents=[
                "请深度阅读这份 PDF，并输出符合系统要求的 Markdown 解析文章。",
                uploaded_file,
            ],
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.4,
            ),
        )
        return (response.text or "").strip() or self._fallback_article(item)

    def _generate_from_html(self, html_path: Path, item: ResearchItem) -> str:
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
        text_path = html_path.with_name("source.txt")
        text_excerpt = ""
        if text_path.exists():
            text_excerpt = text_path.read_text(encoding="utf-8", errors="ignore")[:50000]

        content_parts: list[object] = [
            (
                "下面是技术报告、博客或网页的渲染后内容。"
                "请基于网页文字、截图和图片素材，输出一篇高质量 Markdown 技术解析文章。\n\n"
                f"标题：{item.title}\n"
                f"来源：{item.source_url}\n"
                f"文本抽取：\n{text_excerpt}\n\n"
                f"HTML 片段：\n{html_text[:40000]}"
            )
        ]
        for image_path in self._collect_visual_assets(html_path.parent):
            content_parts.append(self.client.files.upload(file=str(image_path)))

        response = self.client.models.generate_content(
            model=self.settings.gemini_model,
            contents=content_parts,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.4,
            ),
        )
        return (response.text or "").strip() or self._fallback_article(item)

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
