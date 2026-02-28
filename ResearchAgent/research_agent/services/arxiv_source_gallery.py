from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import NamedTuple

import requests


LOGGER = logging.getLogger(__name__)

ARXIV_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf"}
DIRECT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
PRIMARY_ARCHITECTURE_KEYWORDS = {
    "architecture": 8,
    "framework": 7,
    "overview": 7,
    "pipeline": 6,
    "workflow": 6,
    "system": 6,
    "design": 5,
    "method": 5,
    "diagram": 5,
}
SECONDARY_ARCHITECTURE_KEYWORDS = {
    "arch": 4,
    "model": 3,
    "agent": 2,
}
DEPRIORITIZED_KEYWORDS = {
    "benchmark": -4,
    "benchmarks": -4,
    "arena": -3,
    "result": -3,
    "results": -3,
    "comparison": -3,
    "loss": -4,
    "curve": -3,
    "accuracy": -3,
    "evaluation": -2,
}
INCLUDE_GRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?{([^}]+)}")
FIGURE_ENV_RE = re.compile(r"\\begin{figure\*?}(.*?)\\end{figure\*?}", re.DOTALL)
CAPTION_RE = re.compile(r"\\caption(?:\[[^\]]*\])?{(.*?)}", re.DOTALL)


class FigureCandidate(NamedTuple):
    source_path: Path
    caption: str
    score: int


class ArxivSourceGalleryService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.pdftoppm_path = shutil.which("pdftoppm")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ResearchAgent/1.0 (+https://localhost)",
                "Accept": "application/gzip, application/x-gzip, application/octet-stream, */*",
            }
        )

    def ensure_gallery(self, item_dir: Path, arxiv_id: str, limit: int = 8) -> list[dict]:
        if not arxiv_id:
            return []

        asset_root = item_dir / "arxiv-source"
        manifest_path = asset_root / "gallery.json"
        cached = self._load_manifest(manifest_path)
        if cached is not None:
            return cached

        asset_root.mkdir(parents=True, exist_ok=True)
        gallery_entries: list[dict] = []

        try:
            archive_path = asset_root / "source.tar.gz"
            if not archive_path.exists():
                archive_path.write_bytes(self._download_source(arxiv_id))
            extract_dir = asset_root / "extracted"
            if not extract_dir.exists() or not any(extract_dir.iterdir()):
                self._extract_tarball(archive_path, extract_dir)
            gallery_entries = self._materialize_gallery(extract_dir, asset_root / "gallery", limit=limit)
        except Exception as exc:
            LOGGER.warning("Failed to build arXiv source gallery for %s: %s", arxiv_id, exc)

        manifest_path.write_text(json.dumps(gallery_entries, ensure_ascii=False, indent=2), encoding="utf-8")
        return gallery_entries

    def _load_manifest(self, manifest_path: Path) -> list[dict] | None:
        if not manifest_path.exists():
            return None

        try:
            raw_entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

        entries: list[dict] = []
        for entry in raw_entries:
            path = entry.get("path")
            if not path:
                continue
            if not (self.data_dir / path).exists():
                continue
            entries.append(entry)
        return entries

    def _download_source(self, arxiv_id: str) -> bytes:
        response = self.session.get(f"https://arxiv.org/e-print/{arxiv_id}", timeout=90)
        response.raise_for_status()
        return response.content

    def _extract_tarball(self, archive_path: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                member_path = target_dir / member.name
                if not str(member_path.resolve()).startswith(str(target_dir.resolve())):
                    continue
                archive.extract(member, path=target_dir)

    def _materialize_gallery(self, extract_dir: Path, gallery_dir: Path, limit: int) -> list[dict]:
        gallery_dir.mkdir(parents=True, exist_ok=True)
        candidates = self._collect_candidates(extract_dir)
        entries: list[dict] = []

        for index, candidate in enumerate(candidates[:limit], start=1):
            rendered_path = self._render_candidate(candidate.source_path, gallery_dir, index)
            if not rendered_path:
                continue
            entries.append(
                {
                    "title": candidate.caption or self._humanize_name(candidate.source_path.stem),
                    "path": rendered_path.relative_to(self.data_dir).as_posix(),
                    "source_name": candidate.source_path.name,
                }
            )
        return entries

    def _collect_candidates(self, extract_dir: Path) -> list[FigureCandidate]:
        image_files = [
            path
            for path in extract_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in ARXIV_IMAGE_EXTENSIONS
        ]
        if not image_files:
            return []

        path_index = self._build_path_index(extract_dir, image_files)
        candidates: dict[Path, FigureCandidate] = {}

        for tex_path in extract_dir.rglob("*.tex"):
            text = tex_path.read_text(encoding="utf-8", errors="ignore")
            for figure_block in FIGURE_ENV_RE.findall(text):
                caption = self._clean_caption(self._extract_caption(figure_block))
                for include_path in INCLUDE_GRAPHICS_RE.findall(figure_block):
                    resolved = self._resolve_include_path(extract_dir, tex_path.parent, include_path, path_index)
                    if not resolved:
                        continue
                    score = 3 + self._keyword_score(f"{include_path} {caption}")
                    current = candidates.get(resolved)
                    if current is None or score > current.score:
                        candidates[resolved] = FigureCandidate(resolved, caption, score)

        for image_path in image_files:
            if image_path in candidates:
                continue
            score = self._keyword_score(image_path.stem)
            candidates[image_path] = FigureCandidate(
                image_path,
                self._humanize_name(image_path.stem),
                score,
            )

        ordered = sorted(
            candidates.values(),
            key=lambda item: (-item.score, item.source_path.suffix.lower() != ".pdf", item.source_path.name.lower()),
        )
        return ordered

    @staticmethod
    def _build_path_index(extract_dir: Path, image_files: list[Path]) -> dict[str, Path]:
        index: dict[str, Path] = {}
        for path in image_files:
            rel = path.relative_to(extract_dir).as_posix()
            rel_no_ext = path.relative_to(extract_dir).with_suffix("").as_posix()
            index[rel.lower()] = path
            index[rel_no_ext.lower()] = path
            index[path.name.lower()] = path
            index[path.stem.lower()] = path
        return index

    def _resolve_include_path(
        self,
        extract_dir: Path,
        tex_dir: Path,
        include_path: str,
        path_index: dict[str, Path],
    ) -> Path | None:
        normalized = include_path.strip().strip('"').strip("'").replace("\\", "/")
        if not normalized:
            return None

        direct_candidate = (tex_dir / normalized).resolve()
        if str(direct_candidate).startswith(str(extract_dir.resolve())):
            if direct_candidate.is_file() and direct_candidate.suffix.lower() in ARXIV_IMAGE_EXTENSIONS:
                return direct_candidate
            if direct_candidate.suffix:
                fallback = path_index.get(direct_candidate.relative_to(extract_dir).as_posix().lower())
                if fallback:
                    return fallback

        for key in (
            normalized.lower(),
            normalized.rsplit("/", 1)[-1].lower(),
            Path(normalized).stem.lower(),
        ):
            match = path_index.get(key)
            if match:
                return match
        return None

    def _render_candidate(self, source_path: Path, gallery_dir: Path, index: int) -> Path | None:
        stem = f"figure-{index:02d}"
        suffix = source_path.suffix.lower()
        if suffix in DIRECT_IMAGE_EXTENSIONS:
            target = gallery_dir / f"{stem}{suffix}"
            if not target.exists():
                shutil.copyfile(source_path, target)
            return target

        if suffix == ".pdf" and self.pdftoppm_path:
            target_prefix = gallery_dir / stem
            target = target_prefix.with_suffix(".png")
            if not target.exists():
                try:
                    subprocess.run(
                        [
                            self.pdftoppm_path,
                            "-png",
                            "-singlefile",
                            "-f",
                            "1",
                            "-l",
                            "1",
                            "-scale-to",
                            "1800",
                            str(source_path),
                            str(target_prefix),
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception as exc:
                    LOGGER.warning("Failed to render figure PDF %s: %s", source_path, exc)
                    return None
            return target if target.exists() else None
        return None

    @staticmethod
    def _extract_caption(figure_block: str) -> str:
        match = CAPTION_RE.search(figure_block)
        return match.group(1) if match else ""

    @staticmethod
    def _clean_caption(caption: str) -> str:
        if not caption:
            return ""
        cleaned = re.sub(r"\\[a-zA-Z*]+(?:\[[^\]]*\])?", " ", caption)
        cleaned = cleaned.replace("{", " ").replace("}", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    @staticmethod
    def _keyword_score(text: str) -> int:
        normalized = text.lower().replace("_", " ").replace("-", " ")
        score = 0
        for keyword, weight in PRIMARY_ARCHITECTURE_KEYWORDS.items():
            if keyword in normalized:
                score += weight
        for keyword, weight in SECONDARY_ARCHITECTURE_KEYWORDS.items():
            if keyword in normalized:
                score += weight
        for keyword, weight in DEPRIORITIZED_KEYWORDS.items():
            if keyword in normalized:
                score += weight
        if "overall" in normalized:
            score += 4
        if "figure" in normalized:
            score += 1
        return score

    @staticmethod
    def _humanize_name(value: str) -> str:
        readable = re.sub(r"[_\-]+", " ", value).strip()
        return re.sub(r"\s+", " ", readable).title()
