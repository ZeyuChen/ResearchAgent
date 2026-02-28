from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests import Response


LOGGER = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}


@dataclass(slots=True)
class CapturedWebPage:
    url: str
    final_url: str
    title: str
    html: str
    text: str
    source_files: dict[str, bytes]
    image_urls: list[str]
    used_browser: bool


class WebPageCaptureService:
    def __init__(self, session: requests.Session) -> None:
        self.session = session

    def capture(self, url: str) -> CapturedWebPage:
        try:
            return self._capture_with_browser(url)
        except Exception as exc:
            LOGGER.warning("Playwright capture failed for %s: %s", url, exc)
            return self._capture_with_requests(url)

    def _capture_with_browser(self, url: str) -> CapturedWebPage:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 2200})
            page.goto(url, wait_until="networkidle", timeout=45_000)
            page.evaluate(
                """
                async () => {
                  window.scrollTo(0, document.body.scrollHeight);
                  await new Promise((resolve) => setTimeout(resolve, 800));
                  window.scrollTo(0, 0);
                }
                """
            )
            html = page.content()
            final_url = page.url
            title = page.title() or final_url
            screenshot = page.screenshot(full_page=True, type="png")
            text = page.locator("body").inner_text(timeout=5_000)
            raw_image_urls = page.eval_on_selector_all(
                "img",
                "elements => elements.map((img) => img.currentSrc || img.src).filter(Boolean)",
            )
            browser.close()

        image_urls = self._normalize_image_urls(final_url, raw_image_urls)
        source_files: dict[str, bytes] = {
            "source.html": html.encode("utf-8"),
            "source.txt": text.encode("utf-8"),
            "rendered-page.png": screenshot,
        }
        source_files.update(self._download_images(image_urls))
        return CapturedWebPage(
            url=url,
            final_url=final_url,
            title=title.strip(),
            html=html,
            text=text,
            source_files=source_files,
            image_urls=image_urls,
            used_browser=True,
        )

    def _capture_with_requests(self, url: str) -> CapturedWebPage:
        response = self._safe_get(url, timeout=45)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        final_url = str(response.url)
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else final_url
        text = soup.get_text("\n", strip=True)
        raw_image_urls = [node.get("src", "") for node in soup.select("img[src]")]
        image_urls = self._normalize_image_urls(final_url, raw_image_urls)
        source_files: dict[str, bytes] = {
            "source.html": html.encode("utf-8"),
            "source.txt": text.encode("utf-8"),
        }
        source_files.update(self._download_images(image_urls))
        return CapturedWebPage(
            url=url,
            final_url=final_url,
            title=title,
            html=html,
            text=text,
            source_files=source_files,
            image_urls=image_urls,
            used_browser=False,
        )

    @staticmethod
    def _normalize_image_urls(base_url: str, urls: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_url in urls:
            if not raw_url:
                continue
            full_url = urljoin(base_url, raw_url)
            parsed = urlparse(full_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            deduped.append(full_url)
            if len(deduped) >= 4:
                break
        return deduped

    def _download_images(self, image_urls: list[str]) -> dict[str, bytes]:
        downloaded: dict[str, bytes] = {}
        for index, url in enumerate(image_urls, start=1):
            try:
                response = self._safe_get(url, timeout=45)
                response.raise_for_status()
                extension = self._infer_extension(url, response.headers.get("Content-Type", ""))
                downloaded[f"image-{index}{extension}"] = response.content
            except Exception as exc:
                LOGGER.warning("Failed to download image %s: %s", url, exc)
        return downloaded

    def _safe_get(self, url: str, timeout: int) -> Response:
        try:
            return self.session.get(url, timeout=timeout)
        except requests.exceptions.SSLError:
            LOGGER.warning("Retrying %s without TLS verification because certificate validation failed", url)
            return self.session.get(url, timeout=timeout, verify=False)

    @staticmethod
    def _infer_extension(url: str, content_type: str) -> str:
        parsed_path = urlparse(url).path.lower()
        for extension in IMAGE_EXTENSIONS:
            if parsed_path.endswith(extension):
                return extension
        content_type = content_type.lower()
        if "png" in content_type:
            return ".png"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        if "webp" in content_type:
            return ".webp"
        if "gif" in content_type:
            return ".gif"
        sanitized = re.sub(r"[^a-z0-9]+", "", content_type)
        if sanitized:
            return f".{sanitized[:8]}"
        return ".bin"
