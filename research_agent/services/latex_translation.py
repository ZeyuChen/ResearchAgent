from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import requests
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from google.genai import types

from research_agent.config import Settings
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.storage_manager import StorageManager


LOGGER = logging.getLogger(__name__)

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")
DOCUMENTCLASS_RE = re.compile(r"\\documentclass(?:\[[^\]]*\])?{[^}]+}")
BEGIN_DOCUMENT_RE = re.compile(r"\\begin{document}")
TRANSLATION_FENCE_RE = re.compile(r"^```(?:latex|tex)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
SECTION_COMMANDS = (
    "part",
    "chapter",
    "section",
    "subsection",
    "subsubsection",
    "paragraph",
    "subparagraph",
    "title",
)
TRANSLATION_BOUNDARY_RE = re.compile(
    r"^\s*\\(?:chapter|section|subsection|subsubsection|paragraph|subparagraph|begin\{abstract\}|end\{abstract\}|begin\{figure\*?\}|begin\{table\*?\})"
)
ROOT_NAME_HINTS = {
    "main": 12,
    "paper": 10,
    "arxiv": 8,
    "ms": 6,
    "manuscript": 6,
}
ProgressCallback = Callable[[int, str], None]
MAX_INLINE_TEX_CHARS = 14000
TARGET_TEX_CHUNK_CHARS = 10000
MAX_TEX_TRANSLATION_ATTEMPTS = 3
CHINESE_SUPPORT_BLOCK = r"""
% ResearchAgent Chinese translation support begin
\usepackage{iftex}
\ifXeTeX
  \usepackage{fontspec}
  \usepackage{xeCJK}
  \defaultfontfeatures{Ligatures=TeX}
  \IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{}
  \IfFontExistsTF{PingFang SC}{\setCJKmainfont{PingFang SC}}{
    \IfFontExistsTF{Songti SC}{\setCJKmainfont{Songti SC}}{
      \IfFontExistsTF{Heiti SC}{\setCJKmainfont{Heiti SC}}{}
    }
  }
\fi
\ifLuaTeX
  \usepackage{fontspec}
  \usepackage{luatexja-fontspec}
  \defaultfontfeatures{Ligatures=TeX}
  \IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{}
  \IfFontExistsTF{PingFang SC}{\setmainjfont{PingFang SC}}{
    \IfFontExistsTF{Songti SC}{\setmainjfont{Songti SC}}{
      \IfFontExistsTF{Heiti SC}{\setmainjfont{Heiti SC}}{}
    }
  }
\fi
% ResearchAgent Chinese translation support end
""".strip()


class LatexTranslationService:
    def __init__(
        self,
        settings: Settings,
        storage_manager: StorageManager,
        llm_processor: LLMProcessor,
    ) -> None:
        self.settings = settings
        self.storage_manager = storage_manager
        self.llm_processor = llm_processor
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ResearchAgent/1.0 (+https://localhost)",
                "Accept": "application/gzip, application/x-gzip, application/octet-stream, */*",
            }
        )
        self.xelatex_path = shutil.which("xelatex")
        self.lualatex_path = shutil.which("lualatex")
        self.bibtex_path = shutil.which("bibtex")

    @property
    def available(self) -> bool:
        return self.llm_processor.available

    def translate_article(
        self,
        article_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        article = self.storage_manager.load_article(article_id)
        if not article:
            raise ValueError("Article not found")

        arxiv_id = self._infer_arxiv_id(article)
        if not arxiv_id:
            raise ValueError("Only arXiv-backed articles support source-level full-text translation")

        item_dir = self._resolve_item_dir(article)
        if item_dir is None:
            raise ValueError("Article source directory is unavailable")

        if not self.llm_processor.client:
            raise RuntimeError("Gemini API is not configured")

        self._notify(progress_callback, 8, "正在准备 arXiv LaTeX 全文翻译工作区。")
        work_dir = item_dir / "fulltext-translation"
        source_archive = work_dir / "source.tar.gz"
        source_dir = work_dir / "source"
        translated_dir = work_dir / "translated"
        build_dir = work_dir / "build"
        translated_pdf = work_dir / "translated.pdf"
        fallback_pdf = work_dir / "translated-fallback.pdf"
        compile_log_path = work_dir / "compile.log"
        manifest_path = work_dir / "manifest.json"

        work_dir.mkdir(parents=True, exist_ok=True)
        self._reset_translation_workspace(translated_dir, build_dir, translated_pdf, fallback_pdf, compile_log_path)

        if not source_archive.exists():
            self._notify(progress_callback, 14, "正在从 arXiv 下载 LaTeX 源码。")
            source_archive.write_bytes(self._download_source(arxiv_id))
        if not source_dir.exists() or not any(source_dir.iterdir()):
            self._notify(progress_callback, 20, "正在解压 LaTeX 源码包。")
            self._extract_tarball(source_archive, source_dir)

        root_tex = self._detect_root_tex(source_dir)
        if root_tex is None:
            raise RuntimeError("Unable to locate a compilable LaTeX root file in arXiv source")

        shutil.copytree(source_dir, translated_dir, dirs_exist_ok=True)
        translated_root = translated_dir / root_tex.relative_to(source_dir)
        tex_files = sorted(translated_dir.rglob("*.tex"))
        if not tex_files:
            raise RuntimeError("No .tex files found after extracting arXiv source")

        translation_usage = self.llm_processor._empty_usage()
        translated_count = 0
        kept_original_count = 0

        for index, tex_path in enumerate(tex_files, start=1):
            rel_path = tex_path.relative_to(translated_dir)
            progress = 24 + int((index / max(len(tex_files), 1)) * 42)
            self._notify(progress_callback, progress, f"Gemini 正在翻译 {rel_path.as_posix()}。")
            translated_text, usage, changed = self._translate_tex_file(tex_path, root_tex == (source_dir / rel_path))
            translation_usage = self.llm_processor.merge_usage(translation_usage, usage)
            tex_path.write_text(translated_text, encoding="utf-8")
            if changed:
                translated_count += 1
            else:
                kept_original_count += 1

        self._inject_chinese_support(translated_root)

        self._notify(progress_callback, 70, "LaTeX 翻译完成，正在进行中文 PDF 编译。")
        compile_result = self._compile_project(translated_root, build_dir, compile_log_path)

        output_pdf_path = ""
        fallback_used = False
        compiler_name = compile_result.get("compiler", "")
        compile_error = compile_result.get("error", "")

        if compile_result.get("success") and compile_result.get("pdf_path"):
            shutil.copyfile(Path(compile_result["pdf_path"]), translated_pdf)
            output_pdf_path = translated_pdf.relative_to(self.settings.data_dir).as_posix()
            self._notify(progress_callback, 90, f"中文 PDF 编译成功，使用 {compiler_name}。")
        else:
            self._notify(progress_callback, 84, "LaTeX 编译失败，正在生成回退 PDF。")
            fallback_path = self._build_fallback_pdf(translated_dir, fallback_pdf, article)
            if fallback_path:
                fallback_used = True
                output_pdf_path = fallback_path.relative_to(self.settings.data_dir).as_posix()
                if not compiler_name:
                    compiler_name = "fallback-pdf"
            self._notify(progress_callback, 92, "已生成可阅读的回退 PDF。")

        manifest = {
            "available": True,
            "status": "completed" if output_pdf_path else "failed",
            "article_id": article_id,
            "arxiv_id": arxiv_id,
            "root_tex": root_tex.relative_to(source_dir).as_posix(),
            "translated_pdf_path": output_pdf_path,
            "translated_tex_dir": translated_dir.relative_to(self.settings.data_dir).as_posix(),
            "compile_log_path": compile_log_path.relative_to(self.settings.data_dir).as_posix() if compile_log_path.exists() else "",
            "compiler": compiler_name,
            "fallback_used": fallback_used,
            "translated_files": translated_count,
            "kept_original_files": kept_original_count,
            "llm_usage": translation_usage,
            "error": compile_error,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.storage_manager.update_metadata(
            item_dir / "metadata.json",
            {
                "fulltext_translation": manifest,
                "updated_at": manifest["updated_at"],
            },
        )
        self._notify(progress_callback, 96, "全文翻译完成，正在刷新文章数据。")

        updated_article = self.storage_manager.load_article(article_id)
        if not updated_article:
            raise RuntimeError("Translated article metadata could not be reloaded")
        return updated_article

    def _translate_tex_file(self, tex_path: Path, is_root: bool) -> tuple[str, dict, bool]:
        original = tex_path.read_text(encoding="utf-8", errors="ignore")
        if not original.strip():
            return "", self.llm_processor._empty_usage(), False

        if self._looks_like_bibliography_file(tex_path, original):
            return original, self.llm_processor._empty_usage(), False

        if len(original) <= MAX_INLINE_TEX_CHARS:
            try:
                translated, usage = self._translate_with_retries(
                    original,
                    tex_path,
                    is_root=is_root,
                    is_fragment=False,
                    max_output_tokens=8192,
                )
                if self._translation_looks_usable(original, translated, is_root):
                    return translated, usage, translated != original
                LOGGER.warning("Translated TeX output looks unsafe for %s; retrying with chunked mode", tex_path.name)
            except Exception as exc:
                LOGGER.warning("Whole-file TeX translation failed for %s: %s; retrying in chunks", tex_path.name, exc)

        translated, usage, changed = self._translate_tex_in_chunks(original, tex_path)
        if self._translation_looks_usable(original, translated, is_root):
            return translated, usage, changed

        LOGGER.warning("Chunked TeX output still looks unsafe for %s; keeping original file", tex_path.name)
        return original, usage, False

    @staticmethod
    def _build_translation_prompt(
        source_text: str,
        tex_path: Path,
        is_root: bool,
        *,
        is_fragment: bool = False,
        fragment_label: str = "",
    ) -> str:
        role_line = "这是主入口 LaTeX 文件。" if is_root else "这是被主文档引用的 LaTeX 文件。"
        fragment_line = (
            f"这是该文件的一个连续片段（{fragment_label}）。请只翻译并输出这个片段，不要补全文件其他部分。"
            if is_fragment
            else "这是完整文件内容。"
        )
        return (
            "你将收到一个完整的 LaTeX 源文件。请把其中的人类可读英文内容忠实翻译成中文，同时确保输出仍然是可编译的 LaTeX 文件。\n"
            "硬性要求：\n"
            "1. 只翻译自然语言内容：标题、摘要、正文、图注、表格中的文字、列表项、章节标题。\n"
            "2. 保持所有 LaTeX 命令、宏名、环境名、label/ref/cite/eqref、文件路径、图像路径、BibTeX key、数学公式结构原样。\n"
            "3. 对 LLM/ML 专业术语不要过度翻译，常见术语如 Transformer、MoE、RLHF、Agent、token、benchmark、prompt、inference、alignment 可以保留英文或中英并列。\n"
            "4. Reference / Bibliography 章节、thebibliography 环境、\\bibliography 与 \\bibliographystyle 保持原样，不要翻译引用条目。\n"
            "5. 如果某一段内容不确定如何安全翻译，保留原文，不要冒险破坏编译。\n"
            "6. 输出必须只包含完整的 LaTeX 文件内容，不要加解释，不要加代码块。\n\n"
            f"文件：{tex_path.name}\n"
            f"{role_line}\n"
            f"{fragment_line}\n\n"
            f"{source_text}"
        )

    def _translate_with_retries(
        self,
        source_text: str,
        tex_path: Path,
        *,
        is_root: bool,
        is_fragment: bool,
        max_output_tokens: int,
        fragment_label: str = "",
    ) -> tuple[str, dict]:
        last_error: Exception | None = None
        for attempt in range(1, MAX_TEX_TRANSLATION_ATTEMPTS + 1):
            try:
                response = self.llm_processor.client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=self._build_translation_prompt(
                        source_text,
                        tex_path,
                        is_root,
                        is_fragment=is_fragment,
                        fragment_label=fragment_label,
                    ),
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=max_output_tokens,
                    ),
                )
                usage = self.llm_processor.extract_usage(response, self.settings.gemini_model)
                return self._normalize_translated_tex(response.text or ""), usage
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "Gemini TeX translation failed for %s on attempt %s/%s: %s",
                    tex_path.name,
                    attempt,
                    MAX_TEX_TRANSLATION_ATTEMPTS,
                    exc,
                )
        if last_error:
            raise last_error
        raise RuntimeError("Gemini TeX translation failed without an explicit error")

    def _translate_tex_in_chunks(self, original: str, tex_path: Path) -> tuple[str, dict, bool]:
        chunks = self._split_tex_into_chunks(original)
        translated_chunks: list[str] = []
        usage_total = self.llm_processor._empty_usage()
        changed = False

        for index, chunk in enumerate(chunks, start=1):
            fragment_label = f"{index}/{len(chunks)}"
            try:
                translated_chunk, usage = self._translate_with_retries(
                    chunk,
                    tex_path,
                    is_root=False,
                    is_fragment=True,
                    max_output_tokens=4096,
                    fragment_label=fragment_label,
                )
                usage_total = self.llm_processor.merge_usage(usage_total, usage)
                if self._translation_looks_usable(chunk, translated_chunk, False):
                    translated_chunks.append(translated_chunk)
                    changed = changed or translated_chunk != chunk
                    continue
                LOGGER.warning(
                    "Translated TeX fragment looks unsafe for %s chunk %s; keeping original fragment",
                    tex_path.name,
                    fragment_label,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Gemini TeX fragment translation failed for %s chunk %s: %s; keeping original fragment",
                    tex_path.name,
                    fragment_label,
                    exc,
                )
            translated_chunks.append(chunk)

        return "".join(translated_chunks), usage_total, changed

    @staticmethod
    def _split_tex_into_chunks(source_text: str) -> list[str]:
        if len(source_text) <= TARGET_TEX_CHUNK_CHARS:
            return [source_text]

        chunks: list[str] = []
        current_lines: list[str] = []
        current_size = 0
        hard_limit = int(TARGET_TEX_CHUNK_CHARS * 1.3)

        for line in source_text.splitlines(keepends=True):
            line_size = len(line)
            is_boundary = bool(TRANSLATION_BOUNDARY_RE.match(line))
            should_flush = False

            if current_lines and is_boundary and current_size >= TARGET_TEX_CHUNK_CHARS // 2:
                should_flush = True
            elif current_lines and current_size + line_size > hard_limit:
                should_flush = True

            if should_flush:
                chunks.append("".join(current_lines))
                current_lines = []
                current_size = 0

            current_lines.append(line)
            current_size += line_size

        if current_lines:
            chunks.append("".join(current_lines))

        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _normalize_translated_tex(text: str) -> str:
        cleaned = TRANSLATION_FENCE_RE.sub("", text or "").strip()
        if cleaned.startswith("Here is") and "\\documentclass" in cleaned:
            cleaned = cleaned[cleaned.find("\\documentclass") :]
        return cleaned

    @staticmethod
    def _translation_looks_usable(original: str, translated: str, is_root: bool) -> bool:
        if not translated.strip():
            return False
        if is_root and "\\documentclass" not in translated:
            return False
        if BEGIN_DOCUMENT_RE.search(original) and not BEGIN_DOCUMENT_RE.search(translated):
            return False
        if abs(translated.count("{") - translated.count("}")) > 6:
            return False
        return True

    @staticmethod
    def _looks_like_bibliography_file(tex_path: Path, content: str) -> bool:
        lowered_name = tex_path.stem.lower()
        if any(token in lowered_name for token in ("ref", "biblio", "references")):
            return True
        lowered = content.lower()
        return "\\begin{thebibliography}" in lowered and "\\end{thebibliography}" in lowered

    def _inject_chinese_support(self, tex_path: Path) -> None:
        content = tex_path.read_text(encoding="utf-8", errors="ignore")
        if "ResearchAgent Chinese translation support begin" in content:
            return
        if not DOCUMENTCLASS_RE.search(content):
            return
        match = BEGIN_DOCUMENT_RE.search(content)
        if not match:
            return
        updated = f"{content[:match.start()].rstrip()}\n\n{CHINESE_SUPPORT_BLOCK}\n\n{content[match.start():]}"
        tex_path.write_text(updated, encoding="utf-8")

    def _compile_project(self, root_tex: Path, build_dir: Path, log_path: Path) -> dict:
        build_dir.mkdir(parents=True, exist_ok=True)
        compile_logs: list[str] = []

        for compiler_name, compiler_path in self._compiler_chain():
            success, log_text, error = self._run_compiler(compiler_name, compiler_path, root_tex, build_dir)
            compile_logs.append(log_text)
            log_path.write_text("\n\n".join(compile_logs), encoding="utf-8")
            if success:
                pdf_path = build_dir / f"{root_tex.stem}.pdf"
                return {
                    "success": True,
                    "compiler": compiler_name,
                    "pdf_path": pdf_path.as_posix(),
                    "error": "",
                }

            LOGGER.warning("Translated LaTeX compile failed with %s: %s", compiler_name, error)

        final_error = "Translated LaTeX compilation failed. See compile.log for details."
        log_path.write_text("\n\n".join(compile_logs + [final_error]), encoding="utf-8")
        return {
            "success": False,
            "compiler": "",
            "pdf_path": "",
            "error": final_error,
        }

    def _compiler_chain(self) -> list[tuple[str, str]]:
        chain: list[tuple[str, str]] = []
        if self.xelatex_path:
            chain.append(("xelatex", self.xelatex_path))
        if self.lualatex_path:
            chain.append(("lualatex", self.lualatex_path))
        return chain

    def _run_compiler(
        self,
        compiler_name: str,
        compiler_path: str,
        root_tex: Path,
        build_dir: Path,
    ) -> tuple[bool, str, str]:
        compile_cwd = root_tex.parent
        project_root = root_tex.parent
        build_dir.mkdir(parents=True, exist_ok=True)
        log_parts: list[str] = [f"== Compiler: {compiler_name} =="]

        env = os.environ.copy()
        texinputs = env.get("TEXINPUTS", "")
        bibinputs = env.get("BIBINPUTS", "")
        env["TEXINPUTS"] = f"{project_root}{os.pathsep}{texinputs}"
        env["BIBINPUTS"] = f"{project_root}{os.pathsep}{bibinputs}"

        for _ in range(2):
            result = subprocess.run(
                [
                    compiler_path,
                    "-interaction=nonstopmode",
                    "-file-line-error",
                    "-output-directory",
                    str(build_dir),
                    root_tex.name,
                ],
                cwd=compile_cwd,
                env=env,
                capture_output=True,
                text=True,
            )
            log_parts.append(result.stdout)
            log_parts.append(result.stderr)
            if result.returncode != 0:
                return False, "\n".join(part for part in log_parts if part), result.stderr.strip() or result.stdout.strip()

            if self._needs_bibtex(root_tex, build_dir) and self.bibtex_path:
                bib_result = subprocess.run(
                    [self.bibtex_path, root_tex.stem],
                    cwd=build_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                log_parts.append(bib_result.stdout)
                log_parts.append(bib_result.stderr)
                if bib_result.returncode != 0:
                    return False, "\n".join(part for part in log_parts if part), bib_result.stderr.strip() or bib_result.stdout.strip()

        pdf_path = build_dir / f"{root_tex.stem}.pdf"
        if not pdf_path.exists():
            return False, "\n".join(part for part in log_parts if part), "Compiled PDF was not produced"

        return True, "\n".join(part for part in log_parts if part), ""

    def _needs_bibtex(self, root_tex: Path, build_dir: Path) -> bool:
        aux_path = build_dir / f"{root_tex.stem}.aux"
        if not aux_path.exists():
            return False
        aux_text = aux_path.read_text(encoding="utf-8", errors="ignore")
        if "\\bibdata" not in aux_text:
            return False
        if (build_dir / f"{root_tex.stem}.bbl").exists():
            return False
        return any(root_tex.parent.rglob("*.bib"))

    def _build_fallback_pdf(self, translated_dir: Path, target_pdf: Path, article: dict) -> Path | None:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        except Exception:
            pass

        tex_files = sorted(translated_dir.rglob("*.tex"))
        if not tex_files:
            return None

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ResearchAgentTitle",
            parent=styles["Title"],
            fontName="STSong-Light",
            fontSize=18,
            leading=24,
        )
        heading_style = ParagraphStyle(
            "ResearchAgentHeading",
            parent=styles["Heading2"],
            fontName="STSong-Light",
            fontSize=13,
            leading=18,
            spaceBefore=10,
            spaceAfter=6,
        )
        body_style = ParagraphStyle(
            "ResearchAgentBody",
            parent=styles["BodyText"],
            fontName="STSong-Light",
            fontSize=10.5,
            leading=15,
        )

        story = [
            Paragraph(f"{self._escape_text(article.get('title', 'Untitled'))} - 全文中译回退 PDF", title_style),
            Spacer(1, 12),
            Paragraph(
                "说明：原始 LaTeX 中文编译未完全通过。以下为根据已翻译 LaTeX 源码生成的可读回退 PDF，便于先行阅读。原始图表资源与翻译后的 .tex 文件仍已保存在本地。",
                body_style,
            ),
            Spacer(1, 12),
        ]

        for tex_path in tex_files:
            readable_text = self._latex_to_readable_text(tex_path.read_text(encoding="utf-8", errors="ignore"))
            if not readable_text:
                continue
            story.append(Paragraph(self._escape_text(tex_path.relative_to(translated_dir).as_posix()), heading_style))
            for block in readable_text.split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                story.append(Paragraph(self._escape_text(block).replace("\n", "<br/>"), body_style))
                story.append(Spacer(1, 6))
            story.append(PageBreak())

        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        document = SimpleDocTemplate(
            str(target_pdf),
            pagesize=A4,
            leftMargin=48,
            rightMargin=48,
            topMargin=56,
            bottomMargin=48,
            title=f"{article.get('title', 'Untitled')} - 全文中译回退 PDF",
        )
        document.build(story)
        return target_pdf

    @classmethod
    def _latex_to_readable_text(cls, text: str) -> str:
        cleaned = COMMENT_RE.sub("", text)
        for command in SECTION_COMMANDS:
            cleaned = re.sub(
                rf"\\{command}\*?(?:\[[^\]]*\])?{{([^{{}}]*)}}",
                lambda match: f"\n\n{match.group(1).strip()}\n",
                cleaned,
            )
        cleaned = re.sub(r"\\caption(?:\[[^\]]*\])?{([^{}]*)}", lambda match: f"\n图表：{match.group(1).strip()}\n", cleaned)
        cleaned = re.sub(r"\\item\s+", "- ", cleaned)
        cleaned = re.sub(r"\\[a-zA-Z@]+(?:\*?)\s*(?:\[[^\]]*\])?", "", cleaned)
        cleaned = cleaned.replace("{", "").replace("}", "")
        cleaned = cleaned.replace("~", " ").replace("\\", "")
        cleaned = re.sub(r"\$([^$]+)\$", lambda match: match.group(1), cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        lines = [line.strip() for line in cleaned.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    @staticmethod
    def _escape_text(value: str) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _resolve_item_dir(self, article: dict) -> Path | None:
        article_path = article.get("article_path", "")
        if not article_path:
            return None
        return (self.settings.data_dir / article_path).parent

    def _download_source(self, arxiv_id: str) -> bytes:
        response = self.session.get(f"https://arxiv.org/e-print/{arxiv_id}", timeout=90)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _extract_tarball(archive_path: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                member_path = target_dir / member.name
                if not str(member_path.resolve()).startswith(str(target_dir.resolve())):
                    continue
                archive.extract(member, path=target_dir)

    @staticmethod
    def _detect_root_tex(source_dir: Path) -> Path | None:
        candidates: list[tuple[int, Path]] = []
        for tex_path in source_dir.rglob("*.tex"):
            content = tex_path.read_text(encoding="utf-8", errors="ignore")
            if not DOCUMENTCLASS_RE.search(content) or not BEGIN_DOCUMENT_RE.search(content):
                continue
            score = 10
            lowered = tex_path.stem.lower()
            for token, bonus in ROOT_NAME_HINTS.items():
                if token in lowered:
                    score += bonus
            score += min(len(content) // 4000, 8)
            score -= len(tex_path.relative_to(source_dir).parts)
            candidates.append((score, tex_path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1].as_posix()))
        return candidates[0][1]

    @staticmethod
    def _infer_arxiv_id(article: dict) -> str:
        for candidate in (
            article.get("identifier", ""),
            article.get("source_url", ""),
            article.get("meta", {}).get("arxiv_id", ""),
            article.get("fulltext_translation", {}).get("arxiv_id", ""),
        ):
            match = ARXIV_ID_RE.search(str(candidate or ""))
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _notify(progress_callback: ProgressCallback | None, progress: int, message: str) -> None:
        if progress_callback:
            progress_callback(progress, message)

    @staticmethod
    def _reset_translation_workspace(
        translated_dir: Path,
        build_dir: Path,
        translated_pdf: Path,
        fallback_pdf: Path,
        log_path: Path,
    ) -> None:
        for path in (translated_dir, build_dir):
            if path.exists():
                shutil.rmtree(path)
        for file_path in (translated_pdf, fallback_pdf, log_path):
            if file_path.exists():
                file_path.unlink()
