from research_agent.services.markdown_renderer import render_markdown


def test_render_markdown_supports_code_and_math() -> None:
    content = "```python\nprint('hi')\n```\n\n$E=mc^2$"
    rendered = render_markdown(content)
    assert "print" in rendered
    assert "math-inline" in rendered or "math-fallback" in rendered
