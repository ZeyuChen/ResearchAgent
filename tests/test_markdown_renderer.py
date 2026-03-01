from research_agent.services.markdown_renderer import extract_pdf_page_refs, inject_pdf_page_links, render_markdown


def test_render_markdown_supports_code_and_math() -> None:
    content = "```python\nprint('hi')\n```\n\n$E=mc^2$"
    rendered = render_markdown(content)
    assert "print" in rendered
    assert "math-inline" in rendered or "math-fallback" in rendered


def test_pdf_page_refs_are_extracted_and_linked() -> None:
    content = "See [P12] and [P13]."
    rendered = inject_pdf_page_links(render_markdown(content), "/files/demo.pdf")
    assert extract_pdf_page_refs(content) == [12, 13]
    assert 'href="/files/demo.pdf#page=12"' in rendered
