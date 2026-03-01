from __future__ import annotations

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
