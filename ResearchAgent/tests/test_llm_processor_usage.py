from types import SimpleNamespace

from research_agent.config import Settings
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
