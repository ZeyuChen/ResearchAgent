from __future__ import annotations

import html
import re

import markdown
from latex2mathml.converter import convert as latex_to_mathml


DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL)
PAGE_REF_RE = re.compile(r"\[P(\d+)\]")


def render_markdown(markdown_text: str) -> str:
    processed_text, stash = _stash_math(markdown_text)
    rendered = markdown.markdown(
        processed_text,
        extensions=["fenced_code", "tables", "sane_lists", "codehilite"],
        extension_configs={
            "codehilite": {
                "guess_lang": False,
                "noclasses": True,
            }
        },
    )
    for key, value in stash.items():
        rendered = rendered.replace(key, value)
    return rendered


def extract_pdf_page_refs(markdown_text: str) -> list[int]:
    seen: set[int] = set()
    refs: list[int] = []
    for match in PAGE_REF_RE.finditer(markdown_text):
        page = int(match.group(1))
        if page in seen:
            continue
        seen.add(page)
        refs.append(page)
    return refs


def inject_pdf_page_links(rendered_html: str, pdf_url: str | None) -> str:
    if not pdf_url:
        return rendered_html

    def replace(match: re.Match[str]) -> str:
        page = match.group(1)
        return (
            f'<a class="pdf-page-ref" href="{html.escape(pdf_url)}#page={page}" '
            f'target="pdf-viewer" data-page="{page}">P{page}</a>'
        )

    return PAGE_REF_RE.sub(replace, rendered_html)


def _stash_math(markdown_text: str) -> tuple[str, dict[str, str]]:
    stash: dict[str, str] = {}

    def replace_display(match: re.Match[str]) -> str:
        return _stash_formula(match.group(1), stash, display=True)

    def replace_inline(match: re.Match[str]) -> str:
        return _stash_formula(match.group(1), stash, display=False)

    content = DISPLAY_MATH_RE.sub(replace_display, markdown_text)
    content = INLINE_MATH_RE.sub(replace_inline, content)
    return content, stash


def _stash_formula(formula: str, stash: dict[str, str], display: bool) -> str:
    key = f"@@MATH_{len(stash)}@@"
    try:
        mathml = latex_to_mathml(formula.strip())
        wrapper = "div" if display else "span"
        css_class = "math-block" if display else "math-inline"
        stash[key] = f"<{wrapper} class=\"{css_class}\">{mathml}</{wrapper}>"
    except Exception:
        safe = html.escape(formula.strip())
        wrapper = "div" if display else "span"
        stash[key] = f"<{wrapper} class=\"math-fallback\">{safe}</{wrapper}>"
    return key
