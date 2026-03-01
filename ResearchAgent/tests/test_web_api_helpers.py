from research_agent.web.api import build_display_tags, build_visible_source_files


def test_build_display_tags_uses_topic_tags_only() -> None:
    article = {
        "tags": ["manual-web", "webpage"],
        "topic_tags": ["RL", "Agent", "Forge"],
    }

    assert build_display_tags(article) == ["RL", "Agent", "Forge"]


def test_build_visible_source_files_filters_internal_files() -> None:
    source_files = [
        {"name": "source.pdf", "url": "/files/a.pdf"},
        {"name": "source.html", "url": "/files/a.html"},
        {"name": "rendered-page.png", "url": "/files/a.png"},
        {"name": "metadata.json", "url": "/files/a.json"},
    ]

    visible = build_visible_source_files(source_files)

    assert [entry["name"] for entry in visible] == ["source.html", "rendered-page.png"]
