from research_agent.services.manual_ingest import ManualIngestService


def test_extract_arxiv_id_from_abs_url() -> None:
    assert ManualIngestService.extract_arxiv_id("https://arxiv.org/abs/2501.01234v2") == "2501.01234v2"


def test_extract_arxiv_id_ignores_non_arxiv_urls() -> None:
    assert ManualIngestService.extract_arxiv_id("https://example.com/abs/2501.01234") is None
