from __future__ import annotations

from pathlib import Path

from research_agent.config import Settings
from research_agent.services.latex_translation import LatexTranslationService
from research_agent.services.llm_processor import LLMProcessor
from research_agent.services.storage_manager import StorageManager


def test_detect_root_tex_prefers_main_document(tmp_path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "appendix.tex").write_text("\\section{Appendix}\nMore text", encoding="utf-8")
    (source_dir / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
        encoding="utf-8",
    )
    (source_dir / "supplement.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nSupplement\n\\end{document}\n",
        encoding="utf-8",
    )

    root_tex = LatexTranslationService._detect_root_tex(source_dir)

    assert root_tex is not None
    assert root_tex.name == "main.tex"


def test_inject_chinese_support_adds_single_marker(tmp_path) -> None:
    settings = Settings.from_env()
    settings.gemini_api_key = None
    settings.data_dir = tmp_path
    service = LatexTranslationService(
        settings=settings,
        storage_manager=StorageManager(tmp_path),
        llm_processor=LLMProcessor(settings),
    )
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(
        "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
        encoding="utf-8",
    )

    service._inject_chinese_support(tex_path)
    first_pass = tex_path.read_text(encoding="utf-8")
    service._inject_chinese_support(tex_path)
    second_pass = tex_path.read_text(encoding="utf-8")

    assert "ResearchAgent Chinese translation support begin" in first_pass
    assert second_pass.count("ResearchAgent Chinese translation support begin") == 1


def test_split_tex_into_chunks_keeps_section_boundaries() -> None:
    source = (
        "\\section{Intro}\n"
        + ("A" * 6000)
        + "\n"
        + "\\section{Method}\n"
        + ("B" * 6000)
        + "\n"
        + "\\section{Experiments}\n"
        + ("C" * 6000)
        + "\n"
    )

    chunks = LatexTranslationService._split_tex_into_chunks(source)

    assert len(chunks) >= 2
    assert "".join(chunks) == source
    assert chunks[0].startswith("\\section{Intro}")
    assert any(chunk.startswith("\\section{Method}") for chunk in chunks[1:])


def test_bisect_tex_fragment_splits_on_newline_near_middle() -> None:
    source = "line1\nline2\nline3\nline4\n"

    chunks = LatexTranslationService._bisect_tex_fragment(source)

    assert len(chunks) == 2
    assert "".join(chunks) == source


def test_strip_tex_comments_removes_full_and_trailing_comments() -> None:
    source = (
        "% full-line comment\n"
        "\\section{Intro} % keep heading\n"
        "Accuracy is 95\\% today.\n"
        "Text % trailing note\n"
    )

    stripped = LatexTranslationService._strip_tex_comments(source)

    assert "% full-line comment" not in stripped
    assert "\\section{Intro}" in stripped
    assert "keep heading" not in stripped
    assert "95\\%" in stripped
    assert "trailing note" not in stripped
    assert not stripped.startswith("\n")


def test_strip_disabled_packages_removes_fontawesome_and_keeps_others() -> None:
    source = (
        "\\usepackage{graphicx,fontawesome}\n"
        "\\usepackage[table]{xcolor}\n"
    )

    sanitized, removed = LatexTranslationService._strip_disabled_packages(source, {"fontawesome"})

    assert removed == ["fontawesome"]
    assert "\\usepackage{graphicx}" in sanitized
    assert "\\usepackage[table]{xcolor}" in sanitized
    assert "fontawesome" not in sanitized


def test_sanitize_inline_section_commands_removes_headings_from_table_rows() -> None:
    source = "Tool-Decathlon & 39.2 & 23.\\\\subsubsection{代码评测基准评估}\n"

    sanitized, fixes = LatexTranslationService._sanitize_inline_section_commands(source)

    assert fixes == 1
    assert "\\subsubsection" not in sanitized
    assert sanitized.startswith("Tool-Decathlon & 39.2 & 23.")


def test_sanitize_conflicting_cjk_commands_removes_cjk_wrappers() -> None:
    source = "\\begin{CJK}{UTF8}{gbsn}\n正文\n\\end{CJK}\n"

    sanitized, fixes = LatexTranslationService._sanitize_conflicting_cjk_commands(source, {"CJKutf8"})

    assert fixes == 2
    assert "\\begin{CJK}" not in sanitized
    assert "\\end{CJK}" not in sanitized
    assert "正文" in sanitized


def test_sanitize_table_section_commands_removes_section_inside_tabular() -> None:
    source = (
        "\\begin{tabular}{ll}\n"
        "\\subsubsection{错误小节}\n"
        "A & B \\\\\n"
        "\\end{tabular}\n"
    )

    sanitized, fixes = LatexTranslationService._sanitize_table_section_commands(source)

    assert fixes == 1
    assert "\\subsubsection" not in sanitized
    assert "\\begin{tabular}{ll}" in sanitized


def test_protect_fragile_latex_shields_inner_structures_but_keeps_captions_and_footnotes() -> None:
    source = (
        "正文段落。\n"
        "\\footnote{A note that should stay stable.}\n"
        "\\begin{figure}\n"
        "\\caption{Overview figure.}\n"
        "\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}\n"
        "\\end{figure}\n"
        "\\begin{table}\n"
        "\\caption{Result table.}\n"
        "\\begin{tabular}{ll}\nA & B \\\\\n\\end{tabular}\n"
        "\\end{table}\n"
    )

    protected, replacements = LatexTranslationService._protect_fragile_latex(source)
    restored = LatexTranslationService._restore_protected_latex(protected, replacements)

    assert "RAKEEPBLOCKTOKEN" in protected
    assert "\\footnote{" in protected
    assert "\\caption{Overview figure.}" in protected
    assert "\\begin{figure}" in protected
    assert "\\begin{table}" in protected
    assert "\\begin{tikzpicture}" not in protected
    assert "\\begin{tabular}" not in protected
    assert restored == source


def test_protect_fragile_latex_shields_reference_commands() -> None:
    source = (
        "See Section~\\ref{sec:method}.\n"
        "As shown in \\hyperref[fig:pipeline]{Pipeline Figure}.\n"
        "We follow \\citep{foo2024bar}.\n"
    )

    protected, replacements = LatexTranslationService._protect_fragile_latex(source)
    restored = LatexTranslationService._restore_protected_latex(protected, replacements)

    assert "\\ref{" not in protected
    assert "\\hyperref[" not in protected
    assert "\\citep{" not in protected
    assert restored == source


def test_protected_tokens_intact_requires_exact_placeholder_round_trip() -> None:
    replacements = {
        "RAKEEPBLOCKTOKEN0001": "\\begin{figure}A\\end{figure}",
        "RAKEEPBLOCKTOKEN0002": "\\footnote{keep}",
    }

    assert LatexTranslationService._protected_tokens_intact(
        "prefix RAKEEPBLOCKTOKEN0001 mid RAKEEPBLOCKTOKEN0002 suffix",
        replacements,
    )
    assert not LatexTranslationService._protected_tokens_intact(
        "prefix 0001 mid RAKEEPBLOCKTOKEN0002 suffix",
        replacements,
    )


def test_translation_looks_usable_rejects_truncated_structure() -> None:
    original = (
        "\\section{Title}\n"
        "A long paragraph that should remain present after translation.\n"
        "\\paragraph{Point A} Details.\n"
        "\\paragraph{Point B} More details.\n"
        "\\begin{itemize}\n"
        "\\item First\n"
        "\\item Second\n"
        "\\end{itemize}\n"
    )
    translated = (
        "\\section{标题}\n"
        "简短内容。\n"
        "\\paragraph{点 A} 细节。\n"
    )

    assert not LatexTranslationService._translation_looks_usable(original, translated, False)


def test_translation_looks_usable_rejects_missing_reference_commands() -> None:
    original = (
        "\\section{Intro}\n"
        "See Section~\\ref{sec:method} and Figure~\\hyperref[fig:pipeline]{Pipeline}.\n"
        "We follow \\cite{foo2024bar}.\n"
        "\\label{sec:intro}\n"
    )
    translated = (
        "\\section{引言}\n"
        "参见方法部分与图示。\n"
        "我们遵循先前工作。\n"
        "\\label{sec:intro}\n"
    )

    assert not LatexTranslationService._translation_looks_usable(original, translated, False)


def test_compiler_requests_rerun_detects_cross_reference_warnings() -> None:
    log_text = "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right."

    assert LatexTranslationService._compiler_requests_rerun(log_text)


def test_build_gemini_cli_fulltext_prompt_mentions_reference_preservation() -> None:
    prompt = LatexTranslationService._build_gemini_cli_fulltext_prompt(Path("0_main.tex"))

    assert "0_main.tex" in prompt
    assert "\\label" in prompt
    assert "\\hyperref" in prompt
    assert "按文件逐个处理" in prompt
    assert "不要顺手批量重写其他文件" in prompt


def test_build_gemini_cli_file_prompt_limits_scope_to_single_file() -> None:
    prompt = LatexTranslationService._build_gemini_cli_file_prompt(Path("2_pretrain.tex"), Path("0_main.tex"))

    assert "2_pretrain.tex" in prompt
    assert "0_main.tex" in prompt
    assert "只修改当前文件" in prompt
    assert "\\cite" in prompt


def test_build_gemini_cli_repair_prompt_mentions_log_and_minimal_fix() -> None:
    prompt = LatexTranslationService._build_gemini_cli_repair_prompt(
        Path("0_main.tex"),
        "LaTeX Error: Missing } inserted.",
    )

    assert "0_main.tex" in prompt
    assert "最小范围修复" in prompt
    assert "Missing } inserted." in prompt
