from types import SimpleNamespace

from research_agent.config import Settings
from research_agent.models import ResearchItem
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


def test_normalize_web_summary_truncates_to_300_chars() -> None:
    summary = LLMProcessor._normalize_web_summary("A" * 400)

    assert len(summary) <= 303
    assert summary.endswith("...")


def test_extract_summary_text_reads_json_payload() -> None:
    assert LLMProcessor._extract_summary_text('{"summary":"结构化摘要"}') == "结构化摘要"
    assert LLMProcessor._extract_summary_text('Here is the JSON requested: ```json\n{"summary":"带包裹的摘要"}\n```') == "带包裹的摘要"
    assert LLMProcessor._extract_summary_text("Here is the JSON requested: ```") == ""
    assert LLMProcessor._extract_summary_text("{") == ""
    assert LLMProcessor._extract_summary_text("plain text") == "plain text"


def test_extract_topic_tags_deduplicates_and_limits() -> None:
    tags = LLMProcessor._extract_topic_tags('{"tags":["RL","Agent","#RL","Kimi","MoE","Verl","Extra"]}')

    assert tags == ["RL", "Agent", "Kimi", "MoE", "Verl", "Extra"]


def test_fallback_topic_tags_uses_known_entities() -> None:
    tags = LLMProcessor._fallback_topic_tags("Kimi agent uses verl for RL training with MoE support")

    assert tags[:4] == ["RL", "Agent", "MoE", "Kimi"]


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
