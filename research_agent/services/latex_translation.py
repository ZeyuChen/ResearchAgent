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
LEADING_COMMAND_RE = re.compile(r"^\s*(\\[A-Za-z@]+(?:\*?)?)")
TRANSLATION_FENCE_RE = re.compile(r"^```(?:latex|tex)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
USEPACKAGE_LINE_RE = re.compile(r"^(?P<indent>\s*)\\usepackage(?P<options>\[[^\]]*\])?{(?P<packages>[^}]+)}(?P<trailing>\s*)$")
FONTAWESOME_COMMAND_RE = re.compile(r"\\(fa[A-Za-z]+)\b")
BEGIN_ENV_RE = re.compile(r"\\begin{([A-Za-z*]+)}")
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
OPTIONAL_PACKAGE_TFM_PROBES = {
    "fontawesome": "FontAwesome.tfm",
    "fontawesome5": "FontAwesome5FreeSolid-900.tfm",
}
CONFLICTING_CJK_PACKAGES = {"CJKutf8", "CJK"}
FRAGILE_ENVIRONMENT_PATTERNS = (
    re.compile(r"\\begin{longtable}.*?\\end{longtable}", re.DOTALL),
    re.compile(r"\\begin{tabularx}.*?\\end{tabularx}", re.DOTALL),
    re.compile(r"\\begin{tabular\*}.*?\\end{tabular\*}", re.DOTALL),
    re.compile(r"\\begin{tabular}.*?\\end{tabular}", re.DOTALL),
    re.compile(r"\\begin{array}.*?\\end{array}", re.DOTALL),
    re.compile(r"\\begin{tikzpicture}.*?\\end{tikzpicture}", re.DOTALL),
    re.compile(r"\\begin{algorithmic}.*?\\end{algorithmic}", re.DOTALL),
    re.compile(r"\\begin{minted}.*?\\end{minted}", re.DOTALL),
    re.compile(r"\\begin{lstlisting}.*?\\end{lstlisting}", re.DOTALL),
    re.compile(r"\\begin{verbatim\*?}.*?\\end{verbatim\*?}", re.DOTALL),
)
STRICT_PRESERVE_COMMAND_PATTERNS = (
    ("label", re.compile(r"\\label(?:\[[^\]]*\])?{")),
    ("ref", re.compile(r"\\(?:ref|eqref|pageref|autoref|cref|Cref|nameref)\*?(?:\[[^\]]*\])?{")),
    ("hyperref", re.compile(r"\\hyperref(?:\[[^\]]*\])?{")),
    ("cite", re.compile(r"\\(?:[Cc]ite[a-zA-Z*]*|nocite)\b")),
)
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
COMPATIBILITY_MARKER = "% ResearchAgent compatibility fallbacks begin"


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
        self.kpsewhich_path = shutil.which("kpsewhich")
        self.gemini_cli_path = shutil.which("gemini")

    @property
    def available(self) -> bool:
        return self.llm_processor.available

    @property
    def cli_available(self) -> bool:
        return bool(self.gemini_cli_path)

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
        compatibility_notes = self._apply_compile_compatibility_cleaning(translated_dir, translated_root)

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
            "compatibility_notes": compatibility_notes,
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

    def translate_article_with_gemini_cli(
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
        if not self.gemini_cli_path:
            raise RuntimeError("Local Gemini CLI is not installed or not found in PATH")

        item_dir = self._resolve_item_dir(article)
        if item_dir is None:
            raise ValueError("Article source directory is unavailable")

        self._notify(progress_callback, 8, "正在准备 Gemini CLI 全文翻译工作区。")
        work_dir = item_dir / "fulltext-translation"
        source_archive = work_dir / "source.tar.gz"
        source_dir = work_dir / "source"
        translated_dir = work_dir / "translated"
        build_dir = work_dir / "build"
        translated_pdf = work_dir / "translated.pdf"
        fallback_pdf = work_dir / "translated-fallback.pdf"
        compile_log_path = work_dir / "compile.log"
        manifest_path = work_dir / "manifest.json"
        cli_log_path = work_dir / "gemini-cli.log"
        cli_prompt_path = work_dir / "gemini-cli-prompt.txt"

        work_dir.mkdir(parents=True, exist_ok=True)
        self._reset_translation_workspace(translated_dir, build_dir, translated_pdf, fallback_pdf, compile_log_path)
        for file_path in (cli_log_path, cli_prompt_path):
            if file_path.exists():
                file_path.unlink()

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

        prompt_log_parts: list[str] = [self._build_gemini_cli_fulltext_prompt(root_tex.relative_to(source_dir))]
        cli_return_codes: list[int] = []
        cli_warnings: list[str] = []
        translatable_files: list[Path] = []

        for tex_path in tex_files:
            original_text = tex_path.read_text(encoding="utf-8", errors="ignore")
            stripped_text = self._strip_tex_comments(original_text)
            if stripped_text != original_text:
                tex_path.write_text(stripped_text, encoding="utf-8")
            if self._looks_like_bibliography_file(tex_path, stripped_text):
                continue
            translatable_files.append(tex_path)

        total_files = len(translatable_files)
        self._notify(progress_callback, 24, f"Gemini CLI 将按文件逐个翻译，共 {total_files} 个 TeX 文件。")

        for index, tex_path in enumerate(translatable_files, start=1):
            rel_path = tex_path.relative_to(translated_dir)
            progress = 26 + int((index / max(total_files, 1)) * 40)
            self._notify(progress_callback, progress, f"Gemini CLI 正在翻译 {rel_path.as_posix()}。")
            source_path = source_dir / rel_path
            source_text = source_path.read_text(encoding="utf-8", errors="ignore") if source_path.exists() else tex_path.read_text(encoding="utf-8", errors="ignore")
            stripped_source = self._strip_tex_comments(source_text)
            tex_path.write_text(stripped_source, encoding="utf-8")
            result = self._translate_tex_file_with_gemini_cli(
                tex_path=tex_path,
                translated_dir=translated_dir,
                source_text=stripped_source,
                is_root=source_path == root_tex,
                root_tex_relpath=root_tex.relative_to(source_dir),
                log_path=cli_log_path,
                prompt_log_parts=prompt_log_parts,
                progress_callback=progress_callback,
            )
            cli_return_codes.append(int(result["returncode"]))
            warning = str(result["warning"])
            if warning:
                cli_warnings.append(warning)

        cli_prompt_path.write_text("\n\n".join(prompt_log_parts).strip() + "\n", encoding="utf-8")

        self._notify(progress_callback, 68, "Gemini CLI 已完成逐文件翻译，正在进行兼容清洗与编译校验。")
        self._inject_chinese_support(translated_root)
        compatibility_notes = self._apply_compile_compatibility_cleaning(translated_dir, translated_root)

        compile_result = self._compile_project(translated_root, build_dir, compile_log_path)
        repair_attempts = 0
        while not compile_result.get("success") and repair_attempts < 2:
            repair_attempts += 1
            self._notify(
                progress_callback,
                74 + repair_attempts * 4,
                f"Gemini CLI 正在根据编译日志尝试第 {repair_attempts} 次定向修复。",
            )
            repair_result = self._repair_translation_with_gemini_cli(
                translated_dir=translated_dir,
                root_tex=translated_root,
                compile_log_path=compile_log_path,
                prompt_log_parts=prompt_log_parts,
                log_path=cli_log_path,
                progress_callback=progress_callback,
            )
            cli_return_codes.append(int(repair_result["returncode"]))
            warning = str(repair_result["warning"])
            if warning:
                cli_warnings.append(warning)
            compatibility_notes.extend(self._apply_compile_compatibility_cleaning(translated_dir, translated_root))
            compile_result = self._compile_project(translated_root, build_dir, compile_log_path)

        cli_prompt_path.write_text("\n\n".join(prompt_log_parts).strip() + "\n", encoding="utf-8")

        output_pdf_path = ""
        fallback_used = False
        compiler_name = compile_result.get("compiler", "")
        compile_error = compile_result.get("error", "")

        if compile_result.get("success") and compile_result.get("pdf_path"):
            shutil.copyfile(Path(compile_result["pdf_path"]), translated_pdf)
            output_pdf_path = translated_pdf.relative_to(self.settings.data_dir).as_posix()
            self._notify(progress_callback, 90, f"中文 PDF 编译成功，使用 {compiler_name}。")
        else:
            self._notify(progress_callback, 84, "CLI 翻译后的 LaTeX 编译失败，正在生成回退 PDF。")
            fallback_path = self._build_fallback_pdf(translated_dir, fallback_pdf, article)
            if fallback_path:
                fallback_used = True
                output_pdf_path = fallback_path.relative_to(self.settings.data_dir).as_posix()
                if not compiler_name:
                    compiler_name = "fallback-pdf"
            self._notify(progress_callback, 92, "已生成可阅读的回退 PDF。")

        translated_count, kept_original_count = self._count_changed_tex_files(source_dir, translated_dir)
        cli_return_code = max(cli_return_codes) if cli_return_codes else 0
        cli_warning = "；".join(dict.fromkeys(warning for warning in cli_warnings if warning))
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
            "compatibility_notes": compatibility_notes,
            "backend": "gemini-cli",
            "cli_log_path": cli_log_path.relative_to(self.settings.data_dir).as_posix() if cli_log_path.exists() else "",
            "cli_prompt_path": cli_prompt_path.relative_to(self.settings.data_dir).as_posix(),
            "cli_returncode": cli_return_code,
            "cli_warning": cli_warning,
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
        self._notify(progress_callback, 96, "Gemini CLI 全文翻译完成，正在刷新文章数据。")

        updated_article = self.storage_manager.load_article(article_id)
        if not updated_article:
            raise RuntimeError("Translated article metadata could not be reloaded")
        return updated_article

    @staticmethod
    def _build_gemini_cli_fulltext_prompt(root_tex_relpath: Path) -> str:
        root_name = root_tex_relpath.as_posix()
        return (
            "Gemini CLI 全文中译模式：按文件逐个处理这个已解压的 arXiv LaTeX 工程。\n"
            f"主入口文件：`{root_name}`。\n"
            "处理原则：\n"
            "1. 每次只改动当前指定的 `.tex` 文件，不要顺手批量重写其他文件。\n"
            "2. 只翻译人类可读英文内容，必须保留 LaTeX 命令、公式、路径、图像引用、BibTeX key。\n"
            "3. 必须完整保留 `\\label`、`\\ref`、`\\eqref`、`\\pageref`、`\\autoref`、`\\cref`、`\\Cref`、`\\hyperref`、`\\cite`、`\\citet`、`\\citep`、`\\nocite`。\n"
            "4. `Reference / Bibliography / thebibliography` 和 `.bib` 条目不翻译。\n"
            "5. 若遇到不确定的 LaTeX 结构，宁可保留原文，也不要破坏编译。\n"
            "6. 所有文件翻译完成后，再根据编译日志做最小范围修复，并优先保持原版式与跳转功能。\n"
        )

    @staticmethod
    def _build_gemini_cli_file_prompt(tex_relpath: Path, root_tex_relpath: Path) -> str:
        file_name = tex_relpath.as_posix()
        return (
            "你当前位于一个已解压的 arXiv LaTeX 工程目录中。请只编辑一个文件，不要改动其他文件。\n"
            f"主入口文件是 `{root_tex_relpath.as_posix()}`。\n"
            f"当前只允许编辑：`{file_name}`。\n"
            "任务：把这个文件中的人类可读英文内容尽可能完整翻译成中文，并保持其余 LaTeX 结构可编译。\n"
            "硬性要求：\n"
            "1. 只修改当前文件；不要改动其他 `.tex`、图片、样式文件。\n"
            "2. 翻译标题、正文、章节标题、图注、表格标题、脚注、列表项，但不要过度简化。\n"
            "3. 不要改动任何 LaTeX 控制序列、环境名、宏名、数学公式、路径、图片文件名、BibTeX key。\n"
            "4. 必须逐字保留所有交叉引用和引用命令：`\\label`、`\\ref`、`\\eqref`、`\\pageref`、`\\autoref`、`\\cref`、`\\Cref`、`\\hyperref`、`\\cite`、`\\citet`、`\\citep`、`\\nocite`。\n"
            "5. 不要翻译 bibliography、thebibliography 或引用条目本身。\n"
            "6. 对专业术语如 Transformer、MoE、RLHF、Agent、token、benchmark、prompt、inference、alignment 优先保留英文或中英并列。\n"
            "7. 严禁把英文正文改写成更短的英文摘要；核心正文必须翻译成中文。如果某一小段无法安全翻译，保留该小段原文，不要把整节改写成英文总结。\n"
            "8. 不要输出整篇解释，只需完成编辑，然后用一句话说明是否已完成当前文件翻译。\n"
        )

    @staticmethod
    def _build_gemini_cli_repair_prompt(root_tex_relpath: Path, log_excerpt: str) -> str:
        return (
            "你当前位于一个已翻译的 LaTeX 工程目录中。请根据下面的编译日志，只做最小范围修复，使工程重新可编译。\n"
            f"主入口文件：`{root_tex_relpath.as_posix()}`。\n"
            "要求：\n"
            "1. 只修复造成编译失败的 LaTeX 结构问题，优先保持现有翻译内容不变。\n"
            "2. 不要回退整篇翻译，不要大面积重写正文。\n"
            "3. 必须保留交叉引用和 citation 命令。\n"
            "4. 修复后请在当前目录执行 `mkdir -p build`，然后运行 xelatex；若需要 BibTeX，再运行 bibtex 并补跑 xelatex。\n"
            "5. 最后只输出一段简短总结，说明修了哪些文件和是否成功生成 PDF。\n\n"
            "以下是最近的编译日志摘录：\n"
            f"{log_excerpt}"
        )

    def _translate_tex_file_with_gemini_cli(
        self,
        *,
        tex_path: Path,
        translated_dir: Path,
        source_text: str,
        is_root: bool,
        root_tex_relpath: Path,
        log_path: Path,
        prompt_log_parts: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, str | int | bool]:
        rel_path = tex_path.relative_to(translated_dir)
        last_returncode = 0
        for attempt in range(1, 3):
            tex_path.write_text(source_text, encoding="utf-8")
            prompt = self._build_gemini_cli_file_prompt(rel_path, root_tex_relpath)
            prompt_log_parts.append(f"## FILE {rel_path.as_posix()} ATTEMPT {attempt}\n{prompt}")
            result = self._run_gemini_cli_command(
                cwd=translated_dir,
                prompt=prompt,
                log_path=log_path,
                progress_callback=progress_callback,
                timeout=1200,
                append=True,
                log_header=f"== FILE {rel_path.as_posix()} ATTEMPT {attempt} ==",
            )
            last_returncode = int(result.get("returncode", 0))
            updated_text = tex_path.read_text(encoding="utf-8", errors="ignore")
            if self._translation_looks_usable(source_text, updated_text, is_root) and self._cli_output_has_enough_chinese(
                source_text,
                updated_text,
                is_root=is_root,
            ):
                translated = updated_text != source_text
                warning = ""
                if last_returncode != 0:
                    warning = f"{rel_path.as_posix()} 在第 {attempt} 次调用退出码为 {last_returncode}，但文件已保留当前可用结果。"
                return {
                    "returncode": last_returncode,
                    "warning": warning,
                    "translated": translated,
                }

            LOGGER.warning(
                "Gemini CLI produced unusable or insufficiently translated output for %s on attempt %s; reverting and retrying",
                rel_path.as_posix(),
                attempt,
            )

        tex_path.write_text(source_text, encoding="utf-8")
        return {
            "returncode": last_returncode,
            "warning": f"{rel_path.as_posix()} 翻译失败，已回退为原始源码。",
            "translated": False,
        }

    def _repair_translation_with_gemini_cli(
        self,
        *,
        translated_dir: Path,
        root_tex: Path,
        compile_log_path: Path,
        prompt_log_parts: list[str],
        log_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, str | int]:
        log_excerpt = ""
        if compile_log_path.exists():
            compile_log = compile_log_path.read_text(encoding="utf-8", errors="ignore")
            log_excerpt = compile_log[-12000:]
        prompt = self._build_gemini_cli_repair_prompt(root_tex.relative_to(translated_dir), log_excerpt)
        prompt_log_parts.append(f"## REPAIR {datetime.now().isoformat(timespec='seconds')}\n{prompt}")
        result = self._run_gemini_cli_command(
            cwd=translated_dir,
            prompt=prompt,
            log_path=log_path,
            progress_callback=progress_callback,
            timeout=1200,
            append=True,
            log_header="== COMPILE REPAIR ==",
        )
        warning = ""
        if int(result.get("returncode", 0)) != 0:
            warning = f"编译修复阶段 Gemini CLI 以退出码 {int(result.get('returncode', 0))} 结束。"
        return {
            "returncode": int(result.get("returncode", 0)),
            "warning": warning,
        }

    def _run_gemini_cli_command(
        self,
        *,
        cwd: Path,
        prompt: str,
        log_path: Path,
        progress_callback: ProgressCallback | None,
        timeout: int,
        append: bool,
        log_header: str,
    ) -> dict[str, str | int]:
        if not self.gemini_cli_path:
            raise RuntimeError("Local Gemini CLI is not installed or not found in PATH")

        command = [
            self.gemini_cli_path,
            "-p",
            prompt,
            "--approval-mode",
            "yolo",
            "--output-format",
            "text",
        ]
        if self.settings.gemini_model:
            command.extend(["-m", self.settings.gemini_model])

        env = os.environ.copy()
        env.setdefault("NO_COLOR", "1")
        self._notify(progress_callback, 36, "Gemini CLI 正在原地编辑 LaTeX 源码，这一步可能持续数分钟。")
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            combined = "\n".join(
                part
                for part in (
                    (exc.stdout or "").strip(),
                    (exc.stderr or "").strip(),
                )
                if part
            )
            existing = ""
            if append and log_path.exists():
                existing = log_path.read_text(encoding="utf-8", errors="ignore").rstrip()
            payload = "\n\n".join(part for part in (existing, log_header, combined, "TIMEOUT") if part)
            log_path.write_text(payload, encoding="utf-8")
            raise RuntimeError("Gemini CLI 执行超时，请稍后重试或缩小论文范围。") from exc

        combined = "\n".join(
            part
            for part in (
                (result.stdout or "").strip(),
                (result.stderr or "").strip(),
            )
            if part
        )
        existing = ""
        if append and log_path.exists():
            existing = log_path.read_text(encoding="utf-8", errors="ignore").rstrip()
        payload = "\n\n".join(part for part in (existing, log_header, combined) if part)
        log_path.write_text(payload, encoding="utf-8")
        return {
            "returncode": result.returncode,
            "summary": combined[-5000:],
        }

    def _run_gemini_cli_translation(
        self,
        *,
        translated_dir: Path,
        prompt: str,
        log_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, str | int]:
        return self._run_gemini_cli_command(
            cwd=translated_dir,
            prompt=prompt,
            log_path=log_path,
            progress_callback=progress_callback,
            timeout=3600,
            append=False,
            log_header="== FULL PROJECT PASS ==",
        )

    @staticmethod
    def _count_changed_tex_files(source_dir: Path, translated_dir: Path) -> tuple[int, int]:
        translated_count = 0
        kept_original_count = 0
        for translated_path in sorted(translated_dir.rglob("*.tex")):
            source_path = source_dir / translated_path.relative_to(translated_dir)
            translated_text = translated_path.read_text(encoding="utf-8", errors="ignore")
            source_text = source_path.read_text(encoding="utf-8", errors="ignore") if source_path.exists() else ""
            source_text = LatexTranslationService._strip_tex_comments(source_text)
            if translated_text != source_text:
                translated_count += 1
            else:
                kept_original_count += 1
        return translated_count, kept_original_count

    def _translate_tex_file(self, tex_path: Path, is_root: bool) -> tuple[str, dict, bool]:
        original = tex_path.read_text(encoding="utf-8", errors="ignore")
        if not original.strip():
            return "", self.llm_processor._empty_usage(), False

        stripped_original = self._strip_tex_comments(original)
        protected_original, protected_blocks = self._protect_fragile_latex(stripped_original)

        if self._looks_like_bibliography_file(tex_path, stripped_original):
            return stripped_original, self.llm_processor._empty_usage(), stripped_original != original

        if len(protected_original) <= MAX_INLINE_TEX_CHARS:
            try:
                translated, usage = self._translate_with_retries(
                    protected_original,
                    tex_path,
                    is_root=is_root,
                    is_fragment=False,
                    max_output_tokens=8192,
                )
                if not self._protected_tokens_intact(translated, protected_blocks):
                    LOGGER.warning(
                        "Protected LaTeX blocks were modified for %s; retrying with chunked mode",
                        tex_path.name,
                    )
                    raise RuntimeError("Protected LaTeX placeholders were modified")
                translated = self._restore_protected_latex(translated, protected_blocks)
                if self._translation_looks_usable(stripped_original, translated, is_root):
                    return translated, usage, translated != stripped_original
                LOGGER.warning("Translated TeX output looks unsafe for %s; retrying with chunked mode", tex_path.name)
            except Exception as exc:
                LOGGER.warning("Whole-file TeX translation failed for %s: %s; retrying in chunks", tex_path.name, exc)

        translated, usage, changed = self._translate_tex_in_chunks(protected_original, tex_path, is_root=is_root)
        if not self._protected_tokens_intact(translated, protected_blocks):
            LOGGER.warning("Chunked TeX output modified protected blocks for %s; keeping original file", tex_path.name)
            return stripped_original, usage, stripped_original != original
        translated = self._restore_protected_latex(translated, protected_blocks)
        if self._translation_looks_usable(stripped_original, translated, is_root):
            return translated, usage, translated != stripped_original

        LOGGER.warning("Chunked TeX output still looks unsafe for %s; keeping original file", tex_path.name)
        return stripped_original, usage, stripped_original != original

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
            "1. 翻译所有人类可读文本：标题、摘要、正文、图注、表格标题、脚注、列表项、章节标题、作者单位说明。\n"
            "2. 保持所有 LaTeX 命令、宏名、环境名、数学公式、文件路径、图像路径、BibTeX key 原样，不要改动任何控制序列。\n"
            "3. 必须完整保留并原样输出所有交叉引用与引用命令，包括 \\label、\\ref、\\eqref、\\pageref、\\autoref、\\cref、\\Cref、\\hyperref、\\cite、\\citet、\\citep、\\nocite。\n"
            "4. 如果输入中出现 RAKEEPBLOCKTOKENxxxx 这样的占位符，必须逐字原样保留，并且每个占位符只能出现一次，绝不能翻译、删除、拆分或复制。\n"
            "5. Reference / Bibliography 章节、thebibliography 环境、\\bibliography 与 \\bibliographystyle 保持原样，不要翻译引用条目。\n"
            "6. 对 LLM/ML 专业术语不要过度翻译，常见术语如 Transformer、MoE、RLHF、Agent、token、benchmark、prompt、inference、alignment 可以保留英文或中英并列。\n"
            "7. 如果某一段内容不确定如何安全翻译，保留原文，不要冒险破坏编译；但不要省略任何已有文本。\n"
            "8. 输出必须只包含完整的 LaTeX 文件内容，不要加解释，不要加代码块。\n\n"
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

    def _translate_tex_in_chunks(self, original: str, tex_path: Path, *, is_root: bool) -> tuple[str, dict, bool]:
        chunks = self._split_tex_into_chunks(original)
        translated_chunks: list[str] = []
        usage_total = self.llm_processor._empty_usage()
        changed = False

        for index, chunk in enumerate(chunks, start=1):
            fragment_label = f"{index}/{len(chunks)}"
            translated_chunk, usage, chunk_changed = self._translate_tex_fragment(
                chunk,
                tex_path,
                is_root=is_root,
                fragment_label=fragment_label,
            )
            usage_total = self.llm_processor.merge_usage(usage_total, usage)
            translated_chunks.append(translated_chunk)
            changed = changed or chunk_changed

        return "".join(translated_chunks), usage_total, changed

    def _translate_tex_fragment(
        self,
        chunk: str,
        tex_path: Path,
        *,
        is_root: bool,
        fragment_label: str,
        depth: int = 0,
    ) -> tuple[str, dict, bool]:
        usage_total = self.llm_processor._empty_usage()
        try:
            translated_chunk, usage = self._translate_with_retries(
                chunk,
                tex_path,
                is_root=is_root,
                is_fragment=True,
                max_output_tokens=4096,
                fragment_label=fragment_label,
            )
            usage_total = self.llm_processor.merge_usage(usage_total, usage)
            if self._translation_looks_usable(chunk, translated_chunk, False):
                return translated_chunk, usage_total, translated_chunk != chunk
            LOGGER.warning(
                "Translated TeX fragment looks unsafe for %s chunk %s; retrying with finer split",
                tex_path.name,
                fragment_label,
            )
        except Exception as exc:
            LOGGER.warning(
                "Gemini TeX fragment translation failed for %s chunk %s: %s; retrying with finer split",
                tex_path.name,
                fragment_label,
                exc,
            )

        if depth >= 2 or len(chunk) <= 2400:
            return chunk, usage_total, False

        subchunks = self._split_tex_into_chunks(chunk, target_chars=max(2400, min(TARGET_TEX_CHUNK_CHARS, len(chunk) // 2)))
        if len(subchunks) <= 1:
            subchunks = self._bisect_tex_fragment(chunk)
        if len(subchunks) <= 1:
            return chunk, usage_total, False

        translated_parts: list[str] = []
        changed = False
        for index, subchunk in enumerate(subchunks, start=1):
            translated_part, part_usage, part_changed = self._translate_tex_fragment(
                subchunk,
                tex_path,
                is_root=is_root,
                fragment_label=f"{fragment_label}.{index}/{len(subchunks)}",
                depth=depth + 1,
            )
            usage_total = self.llm_processor.merge_usage(usage_total, part_usage)
            translated_parts.append(translated_part)
            changed = changed or part_changed

        return "".join(translated_parts), usage_total, changed

    @staticmethod
    def _split_tex_into_chunks(source_text: str, target_chars: int = TARGET_TEX_CHUNK_CHARS) -> list[str]:
        if len(source_text) <= target_chars:
            return [source_text]

        chunks: list[str] = []
        current_lines: list[str] = []
        current_size = 0
        hard_limit = int(target_chars * 1.3)

        for line in source_text.splitlines(keepends=True):
            line_size = len(line)
            is_boundary = bool(TRANSLATION_BOUNDARY_RE.match(line))
            should_flush = False

            if current_lines and is_boundary and current_size >= target_chars // 2:
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
    def _bisect_tex_fragment(source_text: str) -> list[str]:
        midpoint = len(source_text) // 2
        split_index = source_text.rfind("\n", 0, midpoint)
        if split_index == -1:
            split_index = source_text.find("\n", midpoint)
        if split_index == -1:
            return [source_text]
        return [source_text[: split_index + 1], source_text[split_index + 1 :]]

    @staticmethod
    def _protect_fragile_latex(source_text: str) -> tuple[str, dict[str, str]]:
        protected = source_text
        replacements: dict[str, str] = {}
        counter = 1

        for pattern in FRAGILE_ENVIRONMENT_PATTERNS:
            while True:
                match = pattern.search(protected)
                if not match:
                    break
                token = f"RAKEEPBLOCKTOKEN{counter:04d}"
                replacements[token] = match.group(0)
                protected = f"{protected[:match.start()]}{token}{protected[match.end():]}"
                counter += 1

        for command_name in (
            "label",
            "ref",
            "eqref",
            "pageref",
            "autoref",
            "cref",
            "Cref",
            "nameref",
            "hyperref",
            "cite",
            "citet",
            "citep",
            "citealp",
            "citealt",
            "citeauthor",
            "citeyear",
            "citeyearpar",
            "nocite",
        ):
            protected, replacements, counter = LatexTranslationService._protect_command_arguments(
                protected,
                command_name,
                replacements,
                counter,
            )

        return protected, replacements

    @staticmethod
    def _protect_command_arguments(
        source_text: str,
        command_name: str,
        replacements: dict[str, str],
        counter: int,
    ) -> tuple[str, dict[str, str], int]:
        cursor = 0
        command_prefix = f"\\{command_name}"
        result_parts: list[str] = []

        while cursor < len(source_text):
            start = source_text.find(command_prefix, cursor)
            if start == -1:
                result_parts.append(source_text[cursor:])
                break

            result_parts.append(source_text[cursor:start])
            idx = start + len(command_prefix)
            while idx < len(source_text) and source_text[idx].isspace():
                idx += 1
            if idx < len(source_text) and source_text[idx] == "[":
                idx = LatexTranslationService._find_matching_delimiter(source_text, idx, "[", "]")
                if idx == -1:
                    result_parts.append(source_text[start:])
                    break
            while idx < len(source_text) and source_text[idx].isspace():
                idx += 1
            if idx >= len(source_text) or source_text[idx] != "{":
                result_parts.append(source_text[start:start + len(command_prefix)])
                cursor = start + len(command_prefix)
                continue

            end = LatexTranslationService._find_matching_delimiter(source_text, idx, "{", "}")
            if end == -1:
                result_parts.append(source_text[start:])
                break

            token = f"RAKEEPBLOCKTOKEN{counter:04d}"
            replacements[token] = source_text[start:end]
            result_parts.append(token)
            counter += 1
            cursor = end

        return "".join(result_parts), replacements, counter

    @staticmethod
    def _find_matching_delimiter(source_text: str, start_index: int, opener: str, closer: str) -> int:
        depth = 0
        escaped = False
        for index in range(start_index, len(source_text)):
            char = source_text[index]
            if char == opener and not escaped:
                depth += 1
            elif char == closer and not escaped:
                depth -= 1
                if depth == 0:
                    return index + 1
            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False
        return -1

    @staticmethod
    def _restore_protected_latex(source_text: str, replacements: dict[str, str]) -> str:
        restored = source_text
        for token, original in replacements.items():
            restored = restored.replace(token, original)
        return restored

    @staticmethod
    def _strip_tex_comments(source_text: str) -> str:
        cleaned_lines: list[str] = []
        for line in source_text.splitlines(keepends=True):
            newline = ""
            if line.endswith("\r\n"):
                newline = "\r\n"
                working = line[:-2]
            elif line.endswith("\n"):
                newline = "\n"
                working = line[:-1]
            else:
                working = line

            escaped = False
            comment_index = None
            for index, char in enumerate(working):
                if char == "%" and not escaped:
                    comment_index = index
                    break
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False

            if comment_index is None:
                cleaned_lines.append(working + newline)
                continue

            prefix = working[:comment_index].rstrip()
            if prefix:
                cleaned_lines.append(prefix + newline)

        cleaned = "".join(cleaned_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    @staticmethod
    def _normalize_translated_tex(text: str) -> str:
        cleaned = TRANSLATION_FENCE_RE.sub("", text or "").strip()
        if cleaned.startswith("Here is") and "\\documentclass" in cleaned:
            cleaned = cleaned[cleaned.find("\\documentclass") :]
        return cleaned

    @staticmethod
    def _cli_output_has_enough_chinese(source_text: str, translated_text: str, *, is_root: bool) -> bool:
        original_ascii = sum(1 for ch in source_text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
        translated_cjk = sum(1 for ch in translated_text if "\u4e00" <= ch <= "\u9fff")
        if original_ascii < 180:
            return True
        if translated_cjk <= 0:
            return False
        if is_root:
            return translated_cjk >= 8
        if original_ascii >= 8000:
            return translated_cjk >= 120
        if original_ascii >= 3000:
            return translated_cjk >= 40
        if original_ascii >= 1000:
            return translated_cjk >= 12
        return True

    @staticmethod
    def _protected_tokens_intact(translated: str, replacements: dict[str, str]) -> bool:
        for token in replacements:
            if translated.count(token) != 1:
                return False
        return True

    @staticmethod
    def _translation_looks_usable(original: str, translated: str, is_root: bool) -> bool:
        if not translated.strip():
            return False
        if is_root and "\\documentclass" not in translated:
            return False
        if BEGIN_DOCUMENT_RE.search(original) and not BEGIN_DOCUMENT_RE.search(translated):
            return False
        if translated.count("{") - translated.count("}") != original.count("{") - original.count("}"):
            return False
        original_leading = LEADING_COMMAND_RE.match(original)
        translated_leading = LEADING_COMMAND_RE.match(translated)
        if original_leading:
            if not translated_leading:
                return False
            if original_leading.group(1) != translated_leading.group(1):
                return False
        original_section_counts = LatexTranslationService._count_section_commands(original)
        translated_section_counts = LatexTranslationService._count_section_commands(translated)
        for command, count in original_section_counts.items():
            if translated_section_counts.get(command, 0) < count:
                return False
        original_env_counts = LatexTranslationService._count_begin_environments(original)
        translated_env_counts = LatexTranslationService._count_begin_environments(translated)
        for env_name, count in original_env_counts.items():
            if translated_env_counts.get(env_name, 0) < count:
                return False
        original_preserve_counts = LatexTranslationService._count_strict_preserve_commands(original)
        translated_preserve_counts = LatexTranslationService._count_strict_preserve_commands(translated)
        for name, count in original_preserve_counts.items():
            if translated_preserve_counts.get(name, 0) != count:
                return False
        if translated.count("\\item") < original.count("\\item"):
            return False
        if len(original) >= 800 and len(translated) < max(320, int(len(original) * 0.36)):
            return False
        if len(translated) > max(int(len(original) * 1.8), len(original) + 2400):
            return False
        return True

    @staticmethod
    def _count_strict_preserve_commands(source_text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name, pattern in STRICT_PRESERVE_COMMAND_PATTERNS:
            count = len(pattern.findall(source_text))
            if count:
                counts[name] = count
        return counts

    @staticmethod
    def _count_section_commands(source_text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for command in SECTION_COMMANDS:
            pattern = re.compile(rf"\\{command}\*?(?:\[[^\]]*\])?{{")
            count = len(pattern.findall(source_text))
            if count:
                counts[command] = count
        return counts

    @staticmethod
    def _count_begin_environments(source_text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for env_name in BEGIN_ENV_RE.findall(source_text):
            counts[env_name] = counts.get(env_name, 0) + 1
        return counts

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

    def _apply_compile_compatibility_cleaning(self, translated_dir: Path, root_tex: Path) -> list[str]:
        disabled_packages = self._detect_disabled_optional_packages(translated_dir)
        if not disabled_packages:
            disabled_packages = set()

        notes: list[str] = []
        fallback_commands: set[str] = set()
        tex_files = sorted(translated_dir.rglob("*.tex"))

        for tex_path in tex_files:
            original = tex_path.read_text(encoding="utf-8", errors="ignore")
            sanitized, removed_packages = self._strip_disabled_packages(original, disabled_packages)
            sanitized, cjk_fix_count = self._sanitize_conflicting_cjk_commands(sanitized, disabled_packages)
            sanitized, inline_fix_count = self._sanitize_inline_section_commands(sanitized)
            sanitized, table_fix_count = self._sanitize_table_section_commands(sanitized)
            if removed_packages:
                notes.extend(
                    f"{tex_path.relative_to(translated_dir).as_posix()}: 已降级移除 \\usepackage{{{package}}}"
                    for package in removed_packages
                )
                if any(package in {"fontawesome", "fontawesome5"} for package in removed_packages):
                    fallback_commands.update(FONTAWESOME_COMMAND_RE.findall(original))
            if cjk_fix_count:
                notes.append(
                    f"{tex_path.relative_to(translated_dir).as_posix()}: 已清理 {cjk_fix_count} 处旧 CJK 环境命令。"
                )
            if inline_fix_count:
                notes.append(
                    f"{tex_path.relative_to(translated_dir).as_posix()}: 已修正 {inline_fix_count} 处位于行内的章节命令。"
                )
            if table_fix_count:
                notes.append(
                    f"{tex_path.relative_to(translated_dir).as_posix()}: 已移除 {table_fix_count} 处表格环境内的章节命令。"
                )
            if removed_packages or cjk_fix_count or inline_fix_count or table_fix_count:
                tex_path.write_text(sanitized, encoding="utf-8")

        if fallback_commands:
            self._inject_compatibility_fallbacks(root_tex, fallback_commands)
            notes.append(f"已为 {len(fallback_commands)} 个 FontAwesome 命令注入空实现回退。")

        return notes

    def _detect_disabled_optional_packages(self, translated_dir: Path) -> set[str]:
        disabled: set[str] = set()
        discovered_packages: set[str] = set()
        for tex_path in sorted(translated_dir.rglob("*.tex")):
            for line in tex_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = USEPACKAGE_LINE_RE.match(line.strip())
                if not match:
                    continue
                for package_name in (segment.strip() for segment in match.group("packages").split(",")):
                    if package_name:
                        discovered_packages.add(package_name)

        for package_name, probe in OPTIONAL_PACKAGE_TFM_PROBES.items():
            if package_name not in discovered_packages:
                continue
            if not self._kpsewhich_has_file(probe):
                disabled.add(package_name)

        if (self.xelatex_path or self.lualatex_path) and discovered_packages.intersection(CONFLICTING_CJK_PACKAGES):
            disabled.update(discovered_packages.intersection(CONFLICTING_CJK_PACKAGES))

        return disabled

    def _kpsewhich_has_file(self, filename: str) -> bool:
        if not self.kpsewhich_path:
            return False
        try:
            result = subprocess.run(
                [self.kpsewhich_path, filename],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    @staticmethod
    def _strip_disabled_packages(source_text: str, disabled_packages: set[str]) -> tuple[str, list[str]]:
        if not disabled_packages:
            return source_text, []

        removed: list[str] = []
        sanitized_lines: list[str] = []
        for line in source_text.splitlines(keepends=True):
            stripped = line.rstrip("\r\n")
            newline = line[len(stripped):]
            match = USEPACKAGE_LINE_RE.match(stripped)
            if not match:
                sanitized_lines.append(line)
                continue

            packages = [segment.strip() for segment in match.group("packages").split(",") if segment.strip()]
            kept = [package for package in packages if package not in disabled_packages]
            removed_now = [package for package in packages if package in disabled_packages]
            if not removed_now:
                sanitized_lines.append(line)
                continue

            removed.extend(removed_now)
            if kept:
                rebuilt = (
                    f"{match.group('indent')}\\usepackage"
                    f"{match.group('options') or ''}"
                    f"{{{','.join(kept)}}}"
                    f"{match.group('trailing') or ''}"
                    f"{newline}"
                )
                sanitized_lines.append(rebuilt)
            elif newline:
                sanitized_lines.append(newline)

        unique_removed = list(dict.fromkeys(removed))
        return "".join(sanitized_lines), unique_removed

    @staticmethod
    def _sanitize_inline_section_commands(source_text: str) -> tuple[str, int]:
        fixes = 0
        sanitized_lines: list[str] = []
        pattern = re.compile(
            r"(?P<prefix>\S[^\n]*?)(?P<command>\\(?:chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?(?:\[[^\]]*\])?{[^{}]*})"
        )

        for line in source_text.splitlines(keepends=True):
            updated, count = pattern.subn(r"\g<prefix>", line)
            fixes += count
            sanitized_lines.append(updated)

        return "".join(sanitized_lines), fixes

    @staticmethod
    def _sanitize_conflicting_cjk_commands(source_text: str, disabled_packages: set[str]) -> tuple[str, int]:
        if not disabled_packages.intersection(CONFLICTING_CJK_PACKAGES):
            return source_text, 0

        patterns = (
            r"\\begin\{CJK\*?\}\{[^{}]*\}\{[^{}]*\}",
            r"\\end\{CJK\*?\}",
            r"\\CJKfamily\{[^{}]*\}",
        )
        updated = source_text
        fixes = 0
        for pattern in patterns:
            updated, count = re.subn(pattern, "", updated)
            fixes += count
        return updated, fixes

    @staticmethod
    def _sanitize_table_section_commands(source_text: str) -> tuple[str, int]:
        fixes = 0
        sanitized_lines: list[str] = []
        depth = 0
        begin_pattern = re.compile(r"\\begin{(?:tabular\*?|tabularx|array|longtable)}")
        end_pattern = re.compile(r"\\end{(?:tabular\*?|tabularx|array|longtable)}")
        section_pattern = re.compile(r"^\s*\\(?:chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?(?:\[[^\]]*\])?{[^{}]*}\s*$")

        for line in source_text.splitlines(keepends=True):
            begin_matches = len(begin_pattern.findall(line))
            end_matches = len(end_pattern.findall(line))
            in_table = depth > 0

            if in_table and section_pattern.match(line.strip()):
                fixes += 1
                continue

            sanitized_lines.append(line)
            depth += begin_matches
            depth = max(0, depth - end_matches)

        return "".join(sanitized_lines), fixes

    def _inject_compatibility_fallbacks(self, root_tex: Path, command_names: set[str]) -> None:
        content = root_tex.read_text(encoding="utf-8", errors="ignore")
        if COMPATIBILITY_MARKER in content:
            return
        match = BEGIN_DOCUMENT_RE.search(content)
        if not match:
            return

        stubs = [COMPATIBILITY_MARKER]
        for command_name in sorted(command_names):
            stubs.append(f"\\providecommand{{\\{command_name}}}{{}}")
        stubs.append("\\providecommand{\\faIcon}[1]{}")
        stubs.append("\\providecommand{\\faStyle}[1]{}")
        stubs.append("% ResearchAgent compatibility fallbacks end")
        block = "\n".join(stubs)
        updated = f"{content[:match.start()].rstrip()}\n\n{block}\n\n{content[match.start():]}"
        root_tex.write_text(updated, encoding="utf-8")

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
        followup_passes = 0
        rerun_budget = 2
        last_pass_log = ""
        pass_number = 0

        while True:
            pass_number += 1
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
            last_pass_log = "\n".join(part for part in (result.stdout, result.stderr) if part)
            log_parts.append(f"== {compiler_name} pass {pass_number} ==\n{last_pass_log}".rstrip())
            if result.returncode != 0:
                return False, "\n".join(part for part in log_parts if part), result.stderr.strip() or result.stdout.strip()

            if pass_number == 1:
                if self._needs_bibtex(root_tex, build_dir) and self.bibtex_path:
                    bib_result = subprocess.run(
                        [self.bibtex_path, root_tex.stem],
                        cwd=build_dir,
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    bib_log = "\n".join(part for part in (bib_result.stdout, bib_result.stderr) if part)
                    log_parts.append(f"== bibtex ==\n{bib_log}".rstrip())
                    if bib_result.returncode != 0:
                        return False, "\n".join(part for part in log_parts if part), bib_result.stderr.strip() or bib_result.stdout.strip()
                    followup_passes = 2
                else:
                    followup_passes = 1
                continue

            if followup_passes > 0:
                followup_passes -= 1
                if followup_passes > 0:
                    continue

            if rerun_budget > 0 and self._compiler_requests_rerun(last_pass_log):
                rerun_budget -= 1
                continue
            break

        pdf_path = build_dir / f"{root_tex.stem}.pdf"
        if not pdf_path.exists():
            return False, "\n".join(part for part in log_parts if part), "Compiled PDF was not produced"

        return True, "\n".join(part for part in log_parts if part), ""

    @staticmethod
    def _compiler_requests_rerun(log_text: str) -> bool:
        lowered = log_text.lower()
        return any(
            marker in lowered
            for marker in (
                "rerun to get cross-references right",
                "rerun to get citations correct",
                "citation(s) may have changed",
                "label(s) may have changed",
            )
        )

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
