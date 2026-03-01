from __future__ import annotations

import json

from research_agent.services.arxiv_source_gallery import ArxivSourceGalleryService


def test_ensure_gallery_uses_cached_manifest(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    item_dir = data_dir / "2026-03-01" / "manual-arxiv-demo"
    gallery_dir = item_dir / "arxiv-source" / "gallery"
    gallery_dir.mkdir(parents=True)

    preview = gallery_dir / "figure-01.png"
    preview.write_bytes(b"png")

    manifest = item_dir / "arxiv-source" / "gallery.json"
    manifest.write_text(
        json.dumps([{"title": "Overall Pipeline", "path": "2026-03-01/manual-arxiv-demo/arxiv-source/gallery/figure-01.png"}]),
        encoding="utf-8",
    )

    service = ArxivSourceGalleryService(data_dir)
    monkeypatch.setattr(service, "_download_source", lambda _: (_ for _ in ()).throw(RuntimeError("should not download")))

    entries = service.ensure_gallery(item_dir, "2602.15763")

    assert len(entries) == 1
    assert entries[0]["title"] == "Overall Pipeline"


def test_clean_caption_and_keyword_score() -> None:
    caption = ArxivSourceGalleryService._clean_caption(r"An \textbf{overall} pipeline for the agent system.")

    assert caption == "An overall pipeline for the agent system."
    assert ArxivSourceGalleryService._keyword_score(caption) >= 8
