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


def test_extract_json_payload_supports_wrapped_dict_and_list() -> None:
    wrapped = "Here is the JSON requested: ```json\n{\"chunks\":[{\"heading\":\"1 Intro\"}]}\n```"
    array_payload = "```json\n[{\"heading\":\"2 Method\"}]\n```"

    assert LLMProcessor._extract_json_payload(wrapped) == {"chunks": [{"heading": "1 Intro"}]}
    assert LLMProcessor._extract_json_payload(array_payload) == [{"heading": "2 Method"}]


def test_extract_structured_response_payload_prefers_parsed() -> None:
    response = SimpleNamespace(parsed={"chunks": [{"heading": "1 Intro"}]}, text="")

    assert LLMProcessor._extract_structured_response_payload(response) == {"chunks": [{"heading": "1 Intro"}]}


def test_normalize_pdf_translation_plan_deduplicates_and_limits() -> None:
    payload = {
        "chunks": [
            {"heading": " 1 Intro ", "page_refs": ["p1", "P2"], "translation_scope": " translate intro ", "skip_translation": False},
            {"heading": "1 Intro", "page_refs": ["P1", "P2"], "translation_scope": "translate intro", "skip_translation": False},
            {"heading": "References", "page_refs": "P30", "translation_scope": "keep refs", "skip_translation": True},
        ]
    }

    normalized = LLMProcessor._normalize_pdf_translation_plan(payload)

    assert normalized == [
        {
            "heading": "1 Intro",
            "page_refs": ["P1", "P2"],
            "translation_scope": "translate intro",
            "skip_translation": False,
        },
        {
            "heading": "References",
            "page_refs": ["P30"],
            "translation_scope": "keep refs",
            "skip_translation": True,
        },
    ]


def test_expand_pdf_translation_plan_splits_coarse_joined_headings() -> None:
    chunks = [
        {
            "heading": "Abstract & Introduction",
            "page_refs": ["P1", "P2"],
            "translation_scope": "Translate the abstract and introduction only.",
            "skip_translation": False,
        },
        {
            "heading": "2.2 Pre-training Data & 2.3 Mid-Training",
            "page_refs": ["P6"],
            "translation_scope": "Translate both subsections.",
            "skip_translation": False,
        },
    ]

    expanded = LLMProcessor._expand_pdf_translation_plan(chunks)

    assert [chunk["heading"] for chunk in expanded] == [
        "Abstract",
        "Introduction",
        "2.2 Pre-training Data",
        "2.3 Mid-Training",
    ]
    assert all("当前仅处理其中的" in chunk["translation_scope"] for chunk in expanded)


def test_chunk_translation_is_usable_rejects_summary_like_chunk() -> None:
    chunk = {
        "heading": "Abstract",
        "page_refs": ["P1"],
        "translation_scope": "Translate abstract",
        "skip_translation": False,
    }
    payload = {
        "heading": "核心摘要",
        "page_refs": ["P1"],
        "segments": [
            {
                "original": "",
                "translation": "## 核心摘要\n\n这篇论文主要介绍了 GLM-5。",
            }
        ],
    }

    normalized = LLMProcessor._normalize_pdf_translation_chunk(payload, fallback_chunk=chunk)

    assert normalized["heading"] == "Abstract"
    assert not LLMProcessor._chunk_translation_is_usable(normalized, fallback_chunk=chunk)


def test_chunk_translation_is_usable_accepts_structured_parallel_segments() -> None:
    chunk = {
        "heading": "1 Introduction",
        "page_refs": ["P1", "P2"],
        "translation_scope": "Translate introduction",
        "skip_translation": False,
    }
    payload = {
        "heading": "1 Introduction",
        "page_refs": ["P1", "P2"],
        "segments": [
            {"original": "Sentence one.", "translation": "第一句。"},
            {"original": "Sentence two.", "translation": "第二句。"},
            {"original": "Sentence three.", "translation": "第三句。"},
        ],
    }

    normalized = LLMProcessor._normalize_pdf_translation_chunk(payload, fallback_chunk=chunk)

    assert LLMProcessor._chunk_translation_is_usable(normalized, fallback_chunk=chunk)


def test_sanitize_fallback_chunk_translation_removes_heading_noise() -> None:
    text = "## 核心摘要\n\n---\n\n第一段。\n\n### 小节标题\n\n第二段。"

    cleaned = LLMProcessor._sanitize_fallback_chunk_translation(text)

    assert cleaned == "第一段。\n\n第二段。"


def test_stitch_chunked_pdf_article_combines_sections_naturally() -> None:
    item = ResearchItem(
        source="arxiv",
        title="GLM-5",
        summary="",
        source_url="https://arxiv.org/abs/2602.15763",
        published_at="2026-03-01T00:00:00",
        identifier="glm-5",
    )
    translated_body = LLMProcessor._stitch_chunked_pdf_sections(
        [
            {
                "heading": "1 Introduction",
                "page_refs": ["P1", "P2"],
                "segments": [
                    {"original": "GLM-5 is ...", "translation": "GLM-5 是一套面向 agentic engineering 的模型体系。"},
                    {"original": "It improves ...", "translation": "它在推理与编码场景中强调更强的任务闭环能力。"},
                ],
            }
        ]
    )

    article = LLMProcessor._stitch_chunked_pdf_article(
        item=item,
        summary_text="这篇论文介绍了 GLM-5 的整体目标与关键路线。",
        translated_body=translated_body,
        artifacts_text="- 图 1 展示了训练流程。[P4]",
        commentary_text="- 工程侧的增量主要体现在训练与推理一体化闭环。",
    )

    assert article.startswith("# GLM-5")
    assert "## 核心摘要" in article
    assert "## 1 Introduction" in article
    assert "_页码参考：P1 P2_" in article
    assert "## 关键图表 / 公式 / 表格解读" in article
    assert "## 专家点评：数据 / 算法 / 工程创新" in article
