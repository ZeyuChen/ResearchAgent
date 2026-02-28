from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class PDFPreviewService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.pdftoppm_path = shutil.which("pdftoppm")

    @property
    def available(self) -> bool:
        return bool(self.pdftoppm_path)

    def ensure_previews(self, pdf_path: Path, page_numbers: list[int], limit: int = 4) -> list[dict]:
        if not self.available or not pdf_path.exists():
            return []

        item_dir = pdf_path.parent
        preview_dir = item_dir / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        previews: list[dict] = []
        for page in page_numbers[:limit]:
            target_prefix = preview_dir / f"page-{page:03d}"
            target_file = target_prefix.with_suffix(".png")
            if not target_file.exists():
                self._render_page(pdf_path, page, target_prefix)
            if not target_file.exists():
                continue
            previews.append(
                {
                    "page": page,
                    "path": target_file.relative_to(self.data_dir).as_posix(),
                }
            )
        return previews

    def _render_page(self, pdf_path: Path, page: int, target_prefix: Path) -> None:
        if not self.pdftoppm_path:
            return
        try:
            subprocess.run(
                [
                    self.pdftoppm_path,
                    "-png",
                    "-singlefile",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-scale-to",
                    "1400",
                    str(pdf_path),
                    str(target_prefix),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            LOGGER.warning("Failed to render PDF preview for %s page %s: %s", pdf_path, page, exc)
