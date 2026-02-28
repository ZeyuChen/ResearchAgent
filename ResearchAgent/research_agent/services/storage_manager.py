from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from slugify import slugify

from research_agent.models import ResearchItem, StoredItem


class StorageManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_day_dir(self, target_date: date | None = None) -> Path:
        day = target_date or datetime.now().date()
        day_dir = self.data_dir / day.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir

    def persist_item(self, item: ResearchItem, source_files: dict[str, bytes]) -> StoredItem:
        day_dir = self.get_day_dir()
        item_dir_name = self._build_item_dir_name(item)
        item_dir = day_dir / item_dir_name
        item_dir.mkdir(parents=True, exist_ok=True)

        stored_source_files: dict[str, Path] = {}
        for filename, content in source_files.items():
            target = item_dir / filename
            target.write_bytes(content)
            stored_source_files[filename] = target

        metadata_path = item_dir / "metadata.json"
        article_path = item_dir / "article.md"
        metadata = self._build_metadata(item, item_dir, stored_source_files)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        return StoredItem(
            item=item,
            item_dir=item_dir,
            metadata_path=metadata_path,
            article_path=article_path,
            source_files=stored_source_files,
        )

    def write_article(self, stored_item: StoredItem, markdown_text: str) -> Path:
        stored_item.article_path.write_text(markdown_text, encoding="utf-8")
        self.update_metadata(
            stored_item.metadata_path,
            {
                "article_path": stored_item.article_path.relative_to(self.data_dir).as_posix(),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return stored_item.article_path

    def update_metadata(self, metadata_path: Path, updates: dict) -> dict:
        metadata = self._read_metadata(metadata_path)
        metadata.update(updates)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    def scan_library(self) -> list[dict]:
        items: list[dict] = []
        for metadata_path in self.data_dir.glob("*/*/metadata.json"):
            metadata = self._read_metadata(metadata_path)
            article_path = metadata_path.parent / "article.md"
            metadata["has_article"] = article_path.exists()
            metadata["article_excerpt"] = ""
            if article_path.exists():
                excerpt = article_path.read_text(encoding="utf-8")[:260]
                metadata["article_excerpt"] = excerpt.strip()
            items.append(metadata)
        items.sort(key=lambda row: row.get("published_at", ""), reverse=True)
        return items

    def load_article(self, article_id: str) -> dict | None:
        for metadata_path in self.data_dir.glob("*/*/metadata.json"):
            metadata = self._read_metadata(metadata_path)
            if metadata.get("article_id") != article_id:
                continue
            article_path = metadata_path.parent / "article.md"
            metadata["markdown"] = article_path.read_text(encoding="utf-8") if article_path.exists() else ""
            return metadata
        return None

    def _build_item_dir_name(self, item: ResearchItem) -> str:
        slug = slugify(item.title, max_length=72) or slugify(item.identifier)
        return f"{item.source}-{slug}"

    def _build_metadata(self, item: ResearchItem, item_dir: Path, source_files: dict[str, Path]) -> dict:
        archive_date = item_dir.parent.name
        relative_source_files = [
            {
                "name": name,
                "path": path.relative_to(self.data_dir).as_posix(),
                "kind": path.suffix.lstrip("."),
            }
            for name, path in source_files.items()
        ]
        return {
            "article_id": f"{archive_date}--{item_dir.name}",
            "archive_date": archive_date,
            "source": item.source,
            "identifier": item.identifier,
            "title": item.title,
            "summary": item.summary,
            "source_url": item.source_url,
            "published_at": item.published_at,
            "authors": item.authors,
            "tags": item.tags,
            "meta": item.meta,
            "source_files": relative_source_files,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _read_metadata(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))
