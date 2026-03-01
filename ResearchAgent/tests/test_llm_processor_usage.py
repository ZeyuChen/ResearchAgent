from types import SimpleNamespace

from research_agent.config import Settings
from research_agent.models import ResearchItem, StoredItem
from research_agent.services.llm_processor import LLMProcessor


def test_extract_usage_calculates_costs() -> None:
    settings = Settings.from_env()
    settings.gemini_api_key = None
    processor = LLMProcessor(settings)
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000,
            candidates_token_count=200,
            total_token_count=1200,
        )
    )

    usage = processor._extract_usage(response)
    assert usage["prompt_tokens"] == 1000
    assert usage["output_tokens"] == 200
    assert usage["total_tokens"] == 1200
    assert usage["estimated_cost_usd"] > 0


def test_merge_usage_combines_multiple_calls() -> None:
    merged = LLMProcessor.merge_usage(
        {
            "model": "gemini-3-flash-preview",
            "prompt_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_cost_usd": 0.00005,
            "output_cost_usd": 0.00006,
            "estimated_cost_usd": 0.00011,
            "pricing_basis": "basis",
        },
        {
            "model": "gemini-3-flash-preview",
            "prompt_tokens": 300,
            "output_tokens": 50,
            "total_tokens": 350,
            "input_cost_usd": 0.00015,
            "output_cost_usd": 0.00015,
            "estimated_cost_usd": 0.0003,
            "pricing_basis": "basis",
        },
    )

    assert merged["prompt_tokens"] == 400
    assert merged["output_tokens"] == 70
    assert merged["total_tokens"] == 470
    assert merged["estimated_cost_usd"] == 0.00041


def test_fallback_web_summary_truncates_to_250_chars(tmp_path) -> None:
    source_txt = tmp_path / "source.txt"
    source_txt.write_text("A" * 400, encoding="utf-8")
    item = ResearchItem(
        source="manual-web",
        title="Example",
        summary="",
        source_url="https://example.com",
        published_at="2026-03-01T00:00:00",
        identifier="web-1",
    )
    stored_item = StoredItem(
        item=item,
        item_dir=tmp_path,
        metadata_path=tmp_path / "metadata.json",
        article_path=tmp_path / "article.md",
        source_files={"source.txt": source_txt, "source.html": tmp_path / "source.html"},
    )

    summary = LLMProcessor._fallback_web_summary(stored_item)

    assert len(summary) <= 253
    assert summary.endswith("...")


def test_detects_web_summary_prompt_leak() -> None:
    assert LLMProcessor._looks_like_web_summary_prompt_leak("250 Chinese characters. 2) Don't copy the opening.")
    assert not LLMProcessor._looks_like_web_summary_prompt_leak("Forge 面向大规模 Agent 强化学习，核心在于框架与算法协同。")


def test_extract_summary_text_reads_json_payload() -> None:
    assert LLMProcessor._extract_summary_text('{"summary":"结构化摘要"}') == "结构化摘要"
    assert LLMProcessor._extract_summary_text('Here is the JSON requested: ```json\n{"summary":"带包裹的摘要"}\n```') == "带包裹的摘要"
    assert LLMProcessor._extract_summary_text("plain text") == "plain text"


def test_fallback_article_summary_uses_first_body_paragraph() -> None:
    article = "# 标题\n\n## 核心摘要\n\nForge 是一个面向大规模 Agent 强化学习的训练框架，强调吞吐、稳定性与灵活性的平衡。\n\n## 其他"
    item = ResearchItem(
        source="manual-web",
        title="Forge",
        summary="",
        source_url="https://example.com",
        published_at="2026-03-01T00:00:00",
        identifier="web-2",
    )

    summary = LLMProcessor._fallback_article_summary(article, item)

    assert "Forge 是一个面向大规模 Agent 强化学习的训练框架" in summary
