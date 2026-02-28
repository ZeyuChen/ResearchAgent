from __future__ import annotations

import html
import re

import markdown
from latex2mathml.converter import convert as latex_to_mathml


DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL)


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
