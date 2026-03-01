from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ResearchItem:
    source: str
    title: str
    summary: str
    source_url: str
    published_at: str
    identifier: str
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    pdf_url: str | None = None
    html_url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StoredItem:
    item: ResearchItem
    item_dir: Path
    metadata_path: Path
    article_path: Path
    source_files: dict[str, Path]
