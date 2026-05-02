#!/usr/bin/env python3
"""
manga-dl  —  All-in-one Manga Downloader  (single-file edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sources:
  • NatoManga  (natomanga.com)  — Playwright/CDP mode for Cloudflare bypass
  • AsuraScan  (asuracomic.net) — direct HTTP + astro-island JSON parsing

Install:
  pip install aiohttp aiofiles beautifulsoup4 rich requests
  # For browser/CDP mode:
  pip install playwright && python -m playwright install chromium

Usage:
  python manga_downloader_single.py --interactive
  python manga_downloader_single.py --interactive --source asura
  python manga_downloader_single.py manga_links.csv
  python manga_downloader_single.py --interactive --browser-mode
  python manga_downloader_single.py --interactive --browser-cdp-url http://127.0.0.1:9222
"""

from __future__ import annotations

# ── stdlib ────────────────────────────────────────────────────────────────────
import argparse
import asyncio
import csv
import json
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, NewType, Optional, Set
from urllib.parse import quote, urlparse

# ── third-party ───────────────────────────────────────────────────────────────
import aiofiles
import aiohttp
import requests
from aiohttp import ClientTimeout, TCPConnector
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# ─────────────────────────────────────────────────────────────────────────────
#  Global console
# ─────────────────────────────────────────────────────────────────────────────

console = Console()
URL = NewType("URL", str)

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

NATOMANGA_BASE = "https://www.natomanga.com/"
NATOMANGA_SEARCH = "https://www.natomanga.com/search/story/{}"

ASURA_BASE = "https://asurascans.com/"
ASURA_SEARCH = "https://asurascans.com/browse?search={}"
ASURA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://asurascans.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────────────────────────────────────
#  Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class DownloadError(Exception):
    pass


class NetworkError(DownloadError):
    pass


class RateLimitError(DownloadError):
    def __init__(self, retry_after: int = 1):
        self.retry_after = retry_after


class ParseError(DownloadError):
    pass


class ImageVerificationError(DownloadError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MangaChapter:
    number: float
    url: URL
    folder: Path
    title: Optional[str] = None
    images: Optional[List[URL]] = None
    servers: Optional[List[URL]] = None
    downloaded_images: Optional[Set[URL]] = field(default_factory=set)
    hash_data: Optional[Dict[str, str]] = field(default_factory=dict)


class DownloadStats:
    def __init__(self):
        self.start_time = datetime.now()
        self.total_chapters = 0
        self.completed_chapters = 0
        self.failed_chapters: Set[float] = set()
        self.total_images = 0
        self.downloaded_images = 0
        self.failed_images: Set[str] = set()
        self.download_speeds: List[float] = []
        self.last_backup_time = datetime.now()

    def to_dict(self) -> Dict:
        return {
            "start_time": self.start_time.isoformat(),
            "total_chapters": self.total_chapters,
            "completed_chapters": self.completed_chapters,
            "failed_chapters": list(self.failed_chapters),
            "total_images": self.total_images,
            "downloaded_images": self.downloaded_images,
            "failed_images": list(self.failed_images),
            "download_speeds": self.download_speeds,
            "last_backup_time": self.last_backup_time.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Config:
    max_retries: int = 4
    retry_delay: int = 2
    max_workers: int = 3
    request_delay: float = 1.5
    max_consecutive_failures: int = 5
    base_url: str = NATOMANGA_BASE
    chunk_size: int = 8192
    download_timeout: int = 30
    enable_server_fallback: bool = True
    verify_image_integrity: bool = True
    auto_retry_failed: bool = True
    backup_interval: int = 5
    proxy_list: List[str] = field(default_factory=list)
    user_agents: List[str] = field(default_factory=lambda: list(USER_AGENTS))
    request_headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    # Browser / Playwright
    browser_mode: bool = False
    browser_headless: bool = False
    browser_wait_for_challenge: bool = False
    browser_timeout: int = 30
    browser_profile_dir: str = ".playwright-profile"
    browser_cdp_url: str = ""
    # Source
    source: str = "natomanga"  # "natomanga" | "asura"


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP / image utilities
# ─────────────────────────────────────────────────────────────────────────────


def verify_image_integrity(data: bytes) -> bool:
    if not data or len(data) < 8:
        return False
    if data.startswith(b"\xFF\xD8\xFF"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


def random_headers(base_url: str = NATOMANGA_BASE) -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": base_url,
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────────────────


def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        Path("logs").mkdir(exist_ok=True)
        fh = logging.FileHandler(
            Path("logs") / f"manga_dl_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s - %(filename)s:%(lineno)d")
        )
        logger.addHandler(fh)
    return logger


# ─────────────────────────────────────────────────────────────────────────────
#  NatoManga source  (sync search client)
# ─────────────────────────────────────────────────────────────────────────────


class NatoMangaSource:
    """Sync search + parse client for natomanga.com."""

    def search(self, query: str) -> List[Dict]:
        url = NATOMANGA_SEARCH.format(quote(query.strip()))
        headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": NATOMANGA_BASE}
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return []
        except Exception:
            return []
        return self._parse_results(BeautifulSoup(resp.text, "html.parser"))

    @staticmethod
    def _parse_results(soup: BeautifulSoup) -> List[Dict]:
        results: List[Dict] = []

        for item in soup.select("div.panel_story_list div.story_item"):
            a = item.select_one("h3.story_name a[href]")
            if not a:
                continue
            href = a.get("href", "")
            name = a.get_text(strip=True)
            if not href or not name:
                continue
            link = href if href.startswith("http") else f"https://www.natomanga.com{href}"
            if "/manga/" not in link:
                continue

            latest_chapters = []
            for ch in item.select("em.story_chapter a[href]"):
                ch_href = ch.get("href", "")
                ch_name = ch.get_text(strip=True)
                if ch_href and ch_name:
                    latest_chapters.append({
                        "name": ch_name,
                        "link": ch_href if ch_href.startswith("http") else f"https://www.natomanga.com{ch_href}",
                    })

            meta = [s.get_text(" ", strip=True) for s in item.select("span")]
            author = next((s.replace("Author(s) :", "").strip() for s in meta if "Author(s) :" in s), "")
            updated = next((s.replace("Updated :", "").strip() for s in meta if "Updated :" in s), "")
            views = next((s.replace("View :", "").strip() for s in meta if "View :" in s), "")

            entry = {
                "name": name,
                "link": link,
                "latest_chapters": latest_chapters,
                "author": author,
                "updated": updated,
                "views": views,
                "source": "natomanga",
            }
            if not any(e["link"] == link for e in results):
                results.append(entry)

        if results:
            return results

        # Fallback for older markup
        for a in soup.select('a[href*="/manga/"], a[href*="/manga-"]'):
            href = a.get("href", "")
            name = a.get_text(strip=True)
            if not href or not name or "/chapter-" in href:
                continue
            link = href if href.startswith("http") else f"https://www.natomanga.com{href}"
            if "/manga/" not in link and "/manga-" not in link:
                continue
            entry = {"name": name, "link": link, "source": "natomanga"}
            if entry not in results:
                results.append(entry)

        return results


# ─────────────────────────────────────────────────────────────────────────────
#  AsuraScan source  (sync HTTP + astro-island JSON)
# ─────────────────────────────────────────────────────────────────────────────


class AsuraScanSource:
    """
    Sync client for asuracomic.net.

    • search()          — returns list of series dicts
    • get_chapter_list() — returns sorted List[MangaChapter] for a series URL
    • get_chapter_images() — returns image URL list for a chapter URL
    """

    def search(self, query: str) -> List[Dict]:
        url = ASURA_SEARCH.format(quote(query.strip()))
        try:
            resp = requests.get(url, headers=ASURA_HEADERS, timeout=15)
            if resp.status_code != 200:
                return []
        except Exception:
            return []
        return self._parse_series_list(BeautifulSoup(resp.text, "html.parser"))

    def _parse_series_list(self, soup: BeautifulSoup) -> List[Dict]:
        results: List[Dict] = []
        seen: Set[str] = set()

        # asurascans.com: series links are /comics/{slug}, chapters are /comics/{slug}/chapter/{n}
        for a in soup.select("a[href*='/comics/']"):
            href = a.get("href", "")
            if not href or "/chapter/" in href:
                continue
            if href.startswith("/"):
                href = ASURA_BASE.rstrip("/") + href
            if "/comics/" not in href:
                continue
            if href in seen:
                continue

            # Title: prefer img alt, then first non-empty text node
            img = a.find("img")
            name = ""
            if img:
                name = img.get("alt", "").strip()
            if not name:
                # Strip rating numbers and noise; take the longest word-run
                raw = a.get_text(separator=" ", strip=True)
                # Remove standalone digits / decimal ratings at start
                name = re.sub(r'^[\d.]+\s*', '', raw).strip()[:120]
            if not name:
                continue

            seen.add(href)
            results.append({
                "name": name,
                "link": href,
                "source": "asura",
                "latest_chapters": [],
                "author": "",
                "updated": "",
                "views": "",
            })

        return results

    def get_chapter_list(self, series_url: str) -> List[MangaChapter]:
        try:
            resp = requests.get(series_url, headers=ASURA_HEADERS, timeout=15)
            if resp.status_code != 200:
                return []
        except Exception:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        return self._parse_chapters(soup, series_url)

    @staticmethod
    def _parse_chapters(soup: BeautifulSoup, series_url: str) -> List[MangaChapter]:
        slug = series_url.rstrip("/").split("/")[-1]
        base_folder = Path(_sanitize(slug))
        chapters: List[MangaChapter] = []
        seen: Set[str] = set()

        # asurascans.com chapter links: /comics/{slug}/chapter/{num}
        for a in soup.select("a[href*='/chapter/']"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = ASURA_BASE.rstrip("/") + href
            if href in seen:
                continue
            seen.add(href)

            m = re.search(r"/chapter/(\d+(?:[.\-]\d+)?)/?(?:\?.*)?$", href)
            if not m:
                continue
            token = m.group(1).replace("-", ".")
            try:
                num = float(token)
            except ValueError:
                continue

            folder_name = f"chapter-{int(num):03}" if float(num).is_integer() else f"chapter-{num}"
            chapters.append(
                MangaChapter(
                    number=num,
                    url=URL(href),
                    folder=base_folder / folder_name,
                    downloaded_images=set(),
                    hash_data={},
                )
            )

        chapters.sort(key=lambda c: c.number)
        return chapters

    def get_chapter_images(self, chapter_url: str) -> List[str]:
        try:
            resp = requests.get(chapter_url, headers=ASURA_HEADERS, timeout=20)
            if resp.status_code != 200:
                return []
        except Exception:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        return self._extract_images(soup, chapter_url)

    @staticmethod
    def _extract_images(soup: BeautifulSoup, chapter_url: str) -> List[str]:
        # Method 1: astro-island JSON  (primary — from ManhuaDownloaderV2 pattern)
        island = soup.find("astro-island", attrs={"component-export": "default"})
        if island and island.get("props"):
            try:
                props = json.loads(island["props"])
                return [p[1]["url"][1] for p in props["pages"][1]]
            except (KeyError, IndexError, json.JSONDecodeError, TypeError):
                pass

        # Method 2: dedicated reader / chapter content div with <img> tags
        for selector in (
            "div#chapter-reader",
            "div.chapter-reader",
            "div[class*='reader']",
            "div[class*='chapter-content']",
            "div[class*='page-container']",
        ):
            reader = soup.select_one(selector)
            if reader:
                imgs = [img.get("src", "") for img in reader.find_all("img") if img.get("src")]
                if imgs:
                    return imgs

        # Method 3: last resort — img tags with CDN-like paths
        imgs = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if any(kw in src.lower() for kw in ["/wp-content/", "/manga/", "/chapters/", "cdn", "storage"]):
                imgs.append(src)
        return imgs


# ─────────────────────────────────────────────────────────────────────────────
#  Chapter selection helpers
# ─────────────────────────────────────────────────────────────────────────────


def _norm_num(n: float) -> str:
    v = float(n)
    if v.is_integer():
        return str(int(v))
    return f"{v}".rstrip("0").rstrip(".")


def parse_chapter_selection(selection: str, chapters: List[MangaChapter]) -> List[int]:
    """Return sorted 1-based indices from a user selection string.

    Default mode  — chapter numbers: ``1,4,7``  or  ``10-20``
    Index mode    — prefix with idx/index: ``idx:1-5``  or  ``index:1,3,7``
    All           — ``all``
    """
    sel = selection.strip().lower()
    n = len(chapters)

    if sel == "all":
        return list(range(1, n + 1))

    if sel.startswith("idx:") or sel.startswith("index:"):
        _, raw = sel.split(":", 1)
        return _parse_index_range(raw.strip(), n)

    # Build chapter-number → list-of-indices lookup
    lookup: Dict[str, List[int]] = {}
    for i, ch in enumerate(chapters, 1):
        key = _norm_num(ch.number)
        lookup.setdefault(key, []).append(i)

    selected: Set[int] = set()

    for part in sel.split(","):
        token = part.strip()
        if not token:
            continue

        if "-" in token:
            halves = token.split("-", 1)
            try:
                lo, hi = sorted([float(halves[0].strip()), float(halves[1].strip())])
            except ValueError:
                continue
            for i, ch in enumerate(chapters, 1):
                if lo <= float(ch.number) <= hi:
                    selected.add(i)
            continue

        if token in lookup:
            selected.update(lookup[token])
            continue

        try:
            nv = float(token)
            nk = _norm_num(nv)
            if nk in lookup:
                selected.update(lookup[nk])
        except ValueError:
            pass

    return sorted(selected)


def _parse_index_range(raw: str, upper: int) -> List[int]:
    indices: Set[int] = set()
    if raw == "all":
        return list(range(1, upper + 1))
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            try:
                lo, hi = int(a), int(b)
                if lo > hi:
                    lo, hi = hi, lo
                for v in range(lo, hi + 1):
                    if 1 <= v <= upper:
                        indices.add(v)
            except ValueError:
                continue
        else:
            try:
                v = int(token)
                if 1 <= v <= upper:
                    indices.add(v)
            except ValueError:
                pass
    return sorted(indices)


# ─────────────────────────────────────────────────────────────────────────────
#  Core Downloader
# ─────────────────────────────────────────────────────────────────────────────


class MangaDownloader:
    """
    Async manga downloader.  Supports:
      • NatoManga (natomanga.com) with optional Playwright/CDP browser mode
      • AsuraScan  (asuracomic.net) via direct HTTP + astro-island JSON parsing
    """

    def __init__(self, config: Optional[Config] = None, csv_path: str = "manga_links.csv"):
        self.config = config or Config()
        self.csv_path = Path(csv_path)
        self.logger = setup_logging(__name__)
        self.session: Optional[aiohttp.ClientSession] = None
        self.stats = DownloadStats()
        self.proxy: Optional[str] = None
        # Playwright handles
        self._pw_manager: Optional[Any] = None
        self._browser: Optional[Any] = None
        self._browser_ctx: Optional[Any] = None
        self._browser_page: Optional[Any] = None
        self._browser_lock: asyncio.Lock = asyncio.Lock()
        self._challenge_done = False
        self._soup_cache: Dict[str, BeautifulSoup] = {}
        # Source clients
        self._nato = NatoMangaSource()
        self._asura = AsuraScanSource()

    # ── session / browser init ────────────────────────────────────────────────

    async def init_session(self, enable_browser: Optional[bool] = None):
        if self.session is None:
            timeout = ClientTimeout(total=self.config.download_timeout)
            connector = TCPConnector(limit=self.config.max_workers)
            self.proxy = random.choice(self.config.proxy_list) if self.config.proxy_list else None
            self.session = aiohttp.ClientSession(
                timeout=timeout, connector=connector, trust_env=True
            )
        use_browser = self.config.browser_mode if enable_browser is None else enable_browser
        if use_browser and self._browser_page is None:
            await self._init_browser()

    async def _init_browser(self):
        if async_playwright is None:
            raise DownloadError(
                "Playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  python -m playwright install chromium"
            )

        self._pw_manager = await async_playwright().start()

        # CDP attach — use an already-running Chrome session
        if self.config.browser_cdp_url:
            self._browser = await self._pw_manager.chromium.connect_over_cdp(
                self.config.browser_cdp_url
            )
            ctxs = self._browser.contexts
            self._browser_ctx = ctxs[0] if ctxs else await self._browser.new_context()
            pages = self._browser_ctx.pages
            self._browser_page = pages[0] if pages else await self._browser_ctx.new_page()
            return

        launch_args = ["--disable-blink-features=AutomationControlled"]
        profile_dir = Path(self.config.browser_profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        if not self.config.browser_headless:
            # Try system Chrome first, fall back to bundled Chromium
            for channel in ("chrome", None):
                kwargs: Dict[str, Any] = dict(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    args=launch_args,
                    user_agent=self._nato_headers().get("User-Agent"),
                )
                if channel:
                    kwargs["channel"] = channel
                try:
                    self._browser_ctx = await self._pw_manager.chromium.launch_persistent_context(
                        **kwargs
                    )
                    break
                except Exception:
                    if channel is None:
                        raise
        else:
            self._browser = await self._pw_manager.chromium.launch(
                headless=True, args=launch_args
            )
            self._browser_ctx = await self._browser.new_context(
                user_agent=self._nato_headers().get("User-Agent")
            )

        pages = self._browser_ctx.pages
        self._browser_page = pages[0] if pages else await self._browser_ctx.new_page()
        if not self.config.browser_headless:
            self._browser = self._browser_ctx.browser

    # ── browser helpers ───────────────────────────────────────────────────────

    async def wait_for_browser_challenge(self):
        if (
            not self.config.browser_mode
            or not self.config.browser_wait_for_challenge
            or self.config.browser_headless
            or self._challenge_done
            or self._browser_page is None
        ):
            return
        console.print(
            "\n[bold yellow]Browser mode active.[/]  Complete any anti-bot challenge in the "
            "browser window, then press Enter here."
        )
        if self.config.browser_cdp_url:
            console.print("[dim]Using CDP-attached Chrome session.[/]")
        input("Press Enter after challenge is solved... ")
        self._challenge_done = True

    async def _fetch_soup_browser(self, url: str, scroll_for_chapters: bool = False) -> BeautifulSoup:
        if not self._browser_page:
            raise DownloadError("Browser page not initialized.")
        async with self._browser_lock:
            return await self._fetch_soup_browser_locked(url, scroll_for_chapters)

    async def _fetch_soup_browser_locked(self, url: str, scroll_for_chapters: bool = False) -> BeautifulSoup:
        """Must be called while holding self._browser_lock."""
        await asyncio.sleep(0.35 + random.uniform(0.0, 0.55))
        await self._browser_page.goto(
            str(url), wait_until="domcontentloaded",
            timeout=self.config.browser_timeout * 1000,
        )
        try:
            await self._browser_page.wait_for_load_state(
                "networkidle", timeout=self.config.browser_timeout * 1000
            )
        except Exception:
            pass  # domcontentloaded is enough for most pages

        if scroll_for_chapters:
            prev_count = 0
            no_change = 0
            for _ in range(50):
                count = await self._browser_page.evaluate(
                    """() => {
                        return document.querySelectorAll('a[href*="/chapter-"]').length;
                    }"""
                )
                if count == prev_count:
                    no_change += 1
                    if no_change >= 3:
                        break
                else:
                    no_change = 0
                prev_count = count
                await self._browser_page.evaluate(
                    """() => {
                        window.scrollTo(0, document.body.scrollHeight);
                        const containers = document.querySelectorAll('#chapter-list-container, .chapter-list, div');
                        containers.forEach(c => {
                            if (c.scrollHeight > c.clientHeight) {
                                c.scrollTop = c.scrollHeight;
                            }
                        });
                    }"""
                )
                await asyncio.sleep(1.0)

        content = await self._browser_page.content()
        return BeautifulSoup(content, "html.parser")

    async def search_with_browser(self, query: str) -> List[Dict]:
        """NatoManga search via browser (for Cloudflare-protected pages)."""
        url = NATOMANGA_SEARCH.format(quote(query.strip()))
        soup = await self._fetch_soup_browser(url)
        return NatoMangaSource._parse_results(soup)

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _nato_headers(self) -> Dict[str, str]:
        h = random_headers(self.config.base_url)
        if self.config.request_headers:
            h.update(self.config.request_headers)
        return h

    @asynccontextmanager
    async def _get(self, url: str, **kwargs):
        if self.config.cookies and "cookies" not in kwargs:
            kwargs["cookies"] = self.config.cookies
        result = self.session.get(url, **kwargs)
        if hasattr(result, "__aenter__"):
            async with result as resp:
                yield resp
        else:
            resp = await result
            try:
                yield resp
            finally:
                release = getattr(resp, "release", None)
                if callable(release):
                    release()

    async def _read(self, resp: Any, url: str) -> bytes:
        if resp.status == 429:
            raise RateLimitError(int(resp.headers.get("Retry-After", 1)))
        if resp.status >= 400:
            raise NetworkError(f"HTTP {resp.status} for {url}")
        return await resp.read()

    # ── NatoManga parsing ─────────────────────────────────────────────────────

    async def _fetch_page(self, url: str, scroll: bool = False) -> BeautifulSoup:
        if self.config.browser_mode:
            return await self._fetch_soup_browser(url, scroll_for_chapters=scroll)
        async with self._get(
            url, headers=self._nato_headers(),
            timeout=self.config.download_timeout, proxy=self.proxy,
        ) as resp:
            data = await self._read(resp, url)
            return BeautifulSoup(data, "html.parser")

    @staticmethod
    def _extract_chapter_number(ch_url: str, text: str) -> Optional[float]:
        m = re.search(r"/chapter-(\d+(?:-\d+)?)/?$", str(ch_url).rstrip("/"))
        if m:
            token = m.group(1)
            if "-" in token:
                major, minor = token.split("-", 1)
                try:
                    return float(f"{major}.{minor}")
                except ValueError:
                    return None
            try:
                return float(token)
            except ValueError:
                return None
        try:
            return float(text.strip().replace("Chapter ", ""))
        except ValueError:
            return None

    @staticmethod
    def _chapter_folder_name(ch_url: str, num: float) -> str:
        m = re.search(r"/chapter-(\d+(?:-\d+)?)/?$", str(ch_url).rstrip("/"))
        if m and "-" in m.group(1):
            return f"chapter-{m.group(1)}"
        return f"chapter-{int(num):03}" if float(num).is_integer() else f"chapter-{num}"

    def _parse_nato_chapters(
        self, soup: BeautifulSoup, name: str, base_folder: Path
    ) -> List[MangaChapter]:
        container = soup.find("div", id="chapter-list-container")
        div = container.find("div", class_="chapter-list") if container else None
        if not div:
            div = soup.find("div", class_="chapter-list")
        if not div:
            raise ParseError(f"Chapter list not found for '{name}'")

        chapters: List[MangaChapter] = []
        for row in div.find_all("div", class_="row"):
            a = row.find("a")
            if not a:
                continue
            ch_url = a["href"]
            num = self._extract_chapter_number(ch_url, a.get_text())
            if num is None:
                continue
            chapters.append(
                MangaChapter(
                    number=num,
                    url=URL(ch_url),
                    folder=base_folder / self._chapter_folder_name(ch_url, num),
                    downloaded_images=set(),
                    hash_data={},
                )
            )
        chapters.sort(key=lambda c: c.number)
        return chapters

    def _extract_servers(self, soup: BeautifulSoup, fallback: str) -> List[URL]:
        script = soup.find("script", string=lambda t: t and "var cdns" in t)
        cdns: List[str] = []
        backups: List[str] = []
        if script and script.string:
            m = re.search(r"var cdns = \[(.*?)\];", script.string)
            if m:
                cdns = [u.strip().strip('"') for u in m.group(1).split(",")]
            m = re.search(r"var backupImage = \[(.*?)\];", script.string)
            if m:
                backups = [u.strip().strip('"') for u in m.group(1).split(",")]
        all_servers = cdns + backups
        return [URL(s) for s in all_servers] if all_servers else [URL(fallback)]

    async def list_nato_chapters(self, name: str, url: str) -> List[MangaChapter]:
        safe = _sanitize(name)
        base = self.csv_path.parent / safe
        soup = await self._fetch_page(url, scroll=True)
        servers = self._extract_servers(soup, url)

        # Try to load ALL chapters via the page's data-api-url (infinite-scroll API)
        chapter_soup = soup
        if self._browser_page:
            container = soup.find("div", id="chapter-list-container")
            slug = container.get("data-comic-slug") if container else None
            if slug:
                api_url = f"https://www.natomanga.com/api/manga/{slug}/chapters"
                try:
                    resp_text = await self._browser_page.evaluate(
                        """async (url) => {
                            const r = await fetch(url, {credentials: 'include'});
                            return await r.text();
                        }""",
                        api_url,
                    )
                    if resp_text:
                        try:
                            data = json.loads(resp_text)
                            # JSON response: build HTML soup from data list
                            rows_html = ""
                            for ch in (data if isinstance(data, list) else data.get("chapters", data.get("data", []))):
                                ch_url = ch.get("url") or ch.get("chapter_url") or ch.get("link") or ""
                                ch_name = ch.get("name") or ch.get("title") or ch.get("chapter_name") or ""
                                if ch_url:
                                    rows_html += f'<div class="row"><span><a href="{ch_url}">{ch_name}</a></span></div>'
                            if rows_html:
                                chapter_soup = BeautifulSoup(
                                    f'<div class="chapter-list">{rows_html}</div>', "html.parser"
                                )
                        except json.JSONDecodeError:
                            # HTML fragment — wrap and parse
                            api_soup = BeautifulSoup(resp_text, "html.parser")
                            if api_soup.find("div", class_="row") or api_soup.find("a", href=re.compile(r"/chapter-")):
                                chapter_soup = api_soup
                except Exception:
                    pass  # fall back to original soup

        chapters = self._parse_nato_chapters(chapter_soup, name, base)
        for ch in chapters:
            ch.servers = list(servers)
        return chapters

    # ── NatoManga image download ──────────────────────────────────────────────

    @staticmethod
    def _resolve_img(img_url: URL, server: URL) -> str:
        p = urlparse(str(img_url))
        if p.scheme or p.netloc:
            return str(img_url)
        return f"{str(server).rstrip('/')}/{str(img_url).lstrip('/')}"

    async def _download_image(self, img_url: URL, img_path: Path, servers: List[URL]) -> bool:
        start = time.time()
        for server in servers:
            full = self._resolve_img(img_url, server)
            for attempt in range(self.config.max_retries):
                try:
                    async with self._get(
                        full, headers=self._nato_headers(),
                        timeout=self.config.download_timeout, proxy=self.proxy,
                    ) as resp:
                        data = await self._read(resp, full)
                        if self.config.verify_image_integrity and not verify_image_integrity(data):
                            raise ImageVerificationError(f"Bad image data: {img_path}")
                        img_path.parent.mkdir(parents=True, exist_ok=True)
                        async with aiofiles.open(img_path, "wb") as f:
                            await f.write(data)
                        self.stats.download_speeds.append(len(data) / max(time.time() - start, 0.001))
                        self.stats.downloaded_images += 1
                        return True
                except RateLimitError as e:
                    await asyncio.sleep(e.retry_after)
                except ImageVerificationError:
                    raise
                except Exception:
                    if attempt < self.config.max_retries - 1:
                        await asyncio.sleep(self.config.retry_delay)
        self.stats.failed_images.add(str(img_path))
        return False

    async def _process_nato_chapter(self, chapter: MangaChapter) -> bool:
        try:
            # Fetch metadata + soup (reuse browser page result when cached)
            if self.config.browser_mode:
                soup = await self._fetch_soup_browser(str(chapter.url))
                self._soup_cache[str(chapter.url).rstrip("/")] = soup
                title_el = soup.find("title")
                chapter.title = title_el.text if title_el else f"Chapter {chapter.number}"
                btns = soup.find_all("a", {"class": "server-image-btn"})
                chapter.servers = (
                    [URL(b.get("data-l")) for b in btns if b.get("data-l")]
                    or [URL(str(chapter.url))]
                )
            else:
                async with self._get(
                    str(chapter.url), headers=self._nato_headers(),
                    timeout=self.config.download_timeout, proxy=self.proxy,
                ) as resp:
                    content = await self._read(resp, str(chapter.url))
                    soup = BeautifulSoup(content, "html.parser")
                    title_el = soup.find("title")
                    chapter.title = title_el.text if title_el else f"Chapter {chapter.number}"
                    btns = soup.find_all("a", {"class": "server-image-btn"})
                    chapter.servers = (
                        [URL(b.get("data-l")) for b in btns if b.get("data-l")]
                        or [URL(str(chapter.url))]
                    )

            # Retrieve page content (use cache from metadata fetch when in browser mode)
            cache_key = str(chapter.url).rstrip("/")
            content_soup = self._soup_cache.pop(cache_key, None)
            if content_soup is None:
                if self.config.browser_mode:
                    content_soup = await self._fetch_soup_browser(str(chapter.url))
                else:
                    async with self._get(
                        str(chapter.url), headers=self._nato_headers(),
                        timeout=self.config.download_timeout, proxy=self.proxy,
                    ) as resp:
                        data = await self._read(resp, str(chapter.url))
                        content_soup = BeautifulSoup(data, "html.parser")

            reader = content_soup.find("div", {"class": "container-chapter-reader"})
            if not reader:
                raise ParseError(f"Reader div not found for chapter {chapter.number}")

            imgs = reader.find_all("img")
            self.stats.total_images += len(imgs)
            tasks = []
            for idx, img in enumerate(imgs, 1):
                src = img.get("src")
                if src:
                    ext = Path(urlparse(src).path).suffix.lower() or ".jpg"
                    ip = chapter.folder / f"image-{idx:03}{ext}"
                    if not ip.exists():
                        tasks.append(self._download_image(URL(src), ip, chapter.servers))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                success = all(isinstance(r, bool) and r for r in results)
            else:
                success = True

            if success:
                self.stats.completed_chapters += 1
            else:
                self.stats.failed_chapters.add(chapter.number)
            return success

        except Exception as e:
            self.logger.error(f"Chapter {chapter.number} failed: {e}")
            self.stats.failed_chapters.add(chapter.number)
            return False

    # ── AsuraScan chapter download ────────────────────────────────────────────

    async def _download_asura_image(self, img_url: str, img_path: Path) -> bool:
        for attempt in range(self.config.max_retries):
            try:
                async with self._get(
                    img_url, headers=ASURA_HEADERS,
                    timeout=self.config.download_timeout,
                ) as resp:
                    data = await self._read(resp, img_url)
                    if self.config.verify_image_integrity and not verify_image_integrity(data):
                        raise ImageVerificationError(f"Bad image: {img_url}")
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(img_path, "wb") as f:
                        await f.write(data)
                    self.stats.downloaded_images += 1
                    return True
            except RateLimitError as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay)
        self.stats.failed_images.add(str(img_path))
        return False

    async def _process_asura_chapter(self, chapter: MangaChapter) -> bool:
        images = await asyncio.to_thread(self._asura.get_chapter_images, str(chapter.url))
        if not images:
            self.logger.warning(f"No images for AsuraScan chapter {chapter.number}")
            return False

        chapter.folder.mkdir(parents=True, exist_ok=True)
        self.stats.total_images += len(images)

        tasks = []
        for idx, img_url in enumerate(images, 1):
            ext = Path(urlparse(img_url).path).suffix.lower() or ".jpg"
            ip = chapter.folder / f"image-{idx:03}{ext}"
            if not ip.exists():
                tasks.append(self._download_asura_image(img_url, ip))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success = all(isinstance(r, bool) and r for r in results)
        else:
            success = True  # already downloaded

        if success:
            self.stats.completed_chapters += 1
        else:
            self.stats.failed_chapters.add(chapter.number)
        return success

    # ── Unified process_manga ─────────────────────────────────────────────────

    async def process_manga(
        self,
        name: str,
        url: str,
        source: str = "natomanga",
        start_chapter: Optional[float] = None,
        max_chapters: Optional[float] = None,
        selected_chapter_urls: Optional[Set[str]] = None,
    ) -> Dict:
        self.stats = DownloadStats()
        self._soup_cache.clear()

        # Fetch chapter list
        if source == "asura":
            all_chapters = await asyncio.to_thread(self._asura.get_chapter_list, url)
        else:
            try:
                all_chapters = await self.list_nato_chapters(name, url)
            except Exception as e:
                self.logger.error(f"Failed to fetch chapters for '{name}': {e}")
                return _empty_result(name)

        # Apply filters
        if selected_chapter_urls:
            norm = {str(u).rstrip("/") for u in selected_chapter_urls}
            chapters = [c for c in all_chapters if str(c.url).rstrip("/") in norm]
        else:
            chapters = all_chapters
            if start_chapter is not None:
                chapters = [c for c in chapters if c.number >= start_chapter]
            if max_chapters is not None:
                chapters = [c for c in chapters if c.number <= max_chapters]

        self.stats.total_chapters = len(chapters)

        failed: List[float] = []
        consecutive = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task(f"[cyan]{name}", total=len(chapters))

            for i in range(0, len(chapters), self.config.max_workers):
                batch = chapters[i : i + self.config.max_workers]

                if source == "asura":
                    coros = [self._process_asura_chapter(ch) for ch in batch]
                else:
                    coros = [self._process_nato_chapter(ch) for ch in batch]

                results = await asyncio.gather(*coros)

                for ch, ok in zip(batch, results):
                    if ok:
                        consecutive = 0
                        progress.advance(task_id)
                    else:
                        failed.append(ch.number)
                        consecutive += 1
                        if ch.folder.exists():
                            try:
                                if not any(ch.folder.iterdir()):
                                    ch.folder.rmdir()
                            except OSError:
                                pass

                if consecutive >= self.config.max_consecutive_failures:
                    self.logger.warning(f"Stopping after {consecutive} consecutive failures.")
                    break

                if i % self.config.backup_interval == 0:
                    _write_backup(_sanitize(name), self.stats)

        _write_backup(_sanitize(name), self.stats)

        last = max((c.number for c in chapters if c.number not in failed), default=0.0)
        return {
            "Manga Name": name,
            "Source": source,
            "Total Chapters Attempted": len(chapters),
            "Failed Chapters": failed,
            "Download Statistics": self.stats.to_dict(),
            "Last Successful Chapter": last,
            "Next Chapter": last + 1,
        }

    # ── CSV-based batch run ───────────────────────────────────────────────────

    async def run(
        self,
        start_chapter: Optional[float] = None,
        max_chapters: Optional[float] = None,
    ):
        await self.init_session()
        await self.wait_for_browser_challenge()
        try:
            import pandas as pd

            df = pd.read_csv(self.csv_path)
            if not all(c in df.columns for c in ["Manga Name", "Manga Link"]):
                raise ValueError("CSV must have 'Manga Name' and 'Manga Link' columns")
            df.dropna(subset=["Manga Link"], inplace=True)

            summary_path = self.csv_path.parent / "download_summary.csv"
            existing = pd.DataFrame()
            if summary_path.exists():
                existing = pd.read_csv(summary_path)

            for _, row in df.iterrows():
                name = row["Manga Name"].strip()
                url = row["Manga Link"].strip().split("/chapter-")[0]
                source = self.config.source

                eff_start = start_chapter
                if start_chapter is None and not existing.empty:
                    entry = existing[existing["Manga Name"] == name]
                    if not entry.empty:
                        eff_start = float(entry.iloc[0]["Next Chapter"])

                summary = await self.process_manga(
                    name=name,
                    url=url,
                    source=source,
                    start_chapter=eff_start,
                    max_chapters=max_chapters,
                )

                summary_df = pd.DataFrame([summary])
                if summary_path.exists():
                    updated = pd.concat([existing, summary_df]).drop_duplicates(
                        subset=["Manga Name"], keep="last"
                    )
                else:
                    updated = summary_df
                updated.to_csv(summary_path, index=False)

        except KeyboardInterrupt:
            console.print("\n[yellow]Download interrupted.[/]")
        except Exception as e:
            self.logger.error(f"Run error: {e}")
            raise DownloadError(str(e))
        finally:
            await self.close()

    # ── close ─────────────────────────────────────────────────────────────────

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
        for handle, attr in (
            (self._browser_ctx, "_browser_ctx"),
            (self._browser, "_browser"),
        ):
            if handle:
                try:
                    await handle.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._pw_manager:
            try:
                await self._pw_manager.stop()
            except Exception:
                pass
            self._pw_manager = None
        self._browser_page = None


# ─────────────────────────────────────────────────────────────────────────────
#  Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _sanitize(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip().strip(".")
    return re.sub(r"\s+", " ", s) or "manga"


def _write_backup(safe_name: str, stats: DownloadStats):
    Path("backups").mkdir(exist_ok=True)
    try:
        (Path("backups") / f"{safe_name}_backup.json").write_text(
            json.dumps({"stats": stats.to_dict()}, indent=2)
        )
    except Exception:
        pass


def _empty_result(name: str) -> Dict:
    return {
        "Manga Name": name,
        "Total Chapters Attempted": 0,
        "Failed Chapters": [],
        "Download Statistics": {},
        "Last Successful Chapter": 0,
        "Next Chapter": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Rich terminal UI helpers
# ─────────────────────────────────────────────────────────────────────────────


def print_banner():
    console.print(
        Panel(
            "[bold cyan]manga-dl[/bold cyan]  —  [dim]All-in-one Manga Downloader[/]\n"
            "[dim]Sources: [bold]NatoManga[/bold] · [bold]AsuraScan[/bold][/]",
            title="[bold magenta]♦  manga-dl  ♦[/]",
            border_style="bright_blue",
            padding=(0, 2),
        )
    )


def print_search_results(results: List[Dict]):
    table = Table(
        title="Search Results",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        show_lines=False,
        min_width=60,
    )
    table.add_column("#", style="bold yellow", width=4, justify="right")
    table.add_column("Title", style="bold white", min_width=32)
    table.add_column("Author", style="dim", min_width=15)
    table.add_column("Updated", style="dim", min_width=12)
    table.add_column("Source", style="green", width=11)

    for i, r in enumerate(results, 1):
        chs = r.get("latest_chapters") or []
        latest = ", ".join(c["name"] for c in chs[:2]) if chs else ""
        title_text = Text(r["name"])
        if latest:
            title_text.append(f"\n  {latest}", style="dim")
        table.add_row(
            str(i),
            title_text,
            r.get("author") or "—",
            r.get("updated") or "—",
            r.get("source", "natomanga"),
        )

    console.print(table)


def print_chapters_table(display_chapters: List[MangaChapter]):
    table = Table(
        title=f"Available Chapters  ([dim]{len(display_chapters)} total, latest first[/])",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        show_lines=False,
        min_width=50,
    )
    table.add_column("#", style="bold yellow", width=5, justify="right")
    table.add_column("Chapter", style="bold white", min_width=12)
    table.add_column("URL", style="dim")

    for i, ch in enumerate(display_chapters, 1):
        table.add_row(str(i), f"Chapter {_norm_num(ch.number)}", str(ch.url))

    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
#  Interactive mode
# ─────────────────────────────────────────────────────────────────────────────


async def interactive_mode(downloader: MangaDownloader, source: str = "natomanga"):
    print_banner()

    # Source selection prompt if not predetermined
    if source not in ("natomanga", "asura"):
        console.print("[bold]Available sources:[/]")
        console.print("  [bold yellow]1.[/] NatoManga  [dim](natomanga.com)[/]")
        console.print("  [bold yellow]2.[/] AsuraScan  [dim](asuracomic.net)[/]")
        raw = input("Choose source [1/2, default 1]: ").strip()
        source = "asura" if raw == "2" else "natomanga"

    try:
        # ── Search ─────────────────────────────────────────────────────────────
        while True:
            query = input("\n[Search] Enter manga title: ").strip()
            if query:
                break

        console.print(f"[dim]Searching '{query}' on [bold]{source}[/]...[/]")

        use_browser_search = downloader.config.browser_mode and source == "natomanga"

        if use_browser_search:
            await downloader.init_session()
            await downloader.wait_for_browser_challenge()
            results = await downloader.search_with_browser(query)
        elif source == "asura":
            results = AsuraScanSource().search(query)
        else:
            results = NatoMangaSource().search(query)

        if not results:
            console.print("[bold red]No results found.[/]")
            return

        print_search_results(results)

        # ── Select manga ───────────────────────────────────────────────────────
        selected_idx = None
        while selected_idx is None:
            raw = input(f"Choose manga [1-{len(results)}]: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(results):
                selected_idx = int(raw) - 1
            else:
                console.print("[red]Invalid selection, try again.[/]")

        chosen = results[selected_idx]
        manga_name = chosen["name"]
        manga_url = chosen["link"]
        console.print(f"\n[bold green]Selected:[/] {manga_name}")

        # ── Init session ────────────────────────────────────────────────────────
        if not use_browser_search:
            await downloader.init_session()
            await downloader.wait_for_browser_challenge()

        # ── Fetch chapter list ─────────────────────────────────────────────────
        console.print("[dim]Fetching chapter list...[/]")

        if source == "asura":
            chapters = await asyncio.to_thread(AsuraScanSource().get_chapter_list, manga_url)
        else:
            chapters = await downloader.list_nato_chapters(manga_name, manga_url)

        if not chapters:
            console.print("[bold red]No chapters found.[/]")
            return

        display = list(reversed(chapters))  # latest first for display
        print_chapters_table(display)

        # ── Select chapters ────────────────────────────────────────────────────
        console.print(
            "\n[dim]Selection examples: "
            "[bold]1,4,7[/]  |  [bold]10-20[/]  |  "
            "[bold]idx:1-5[/]  |  [bold]all[/][/]"
        )
        raw_sel = input("Select chapters: ").strip()
        sel_indices = parse_chapter_selection(raw_sel, display)

        if not sel_indices:
            console.print("[bold red]No valid chapters selected.[/]")
            return

        selected_urls = {str(display[i - 1].url) for i in sel_indices}
        console.print(f"\n[bold]Downloading [cyan]{len(sel_indices)}[/] chapter(s)...[/]")

        # ── Download ───────────────────────────────────────────────────────────
        summary = await downloader.process_manga(
            name=manga_name,
            url=manga_url,
            source=source,
            selected_chapter_urls=selected_urls,
        )

        # ── Summary panel ──────────────────────────────────────────────────────
        total = summary.get("Total Chapters Attempted", 0)
        failed = summary.get("Failed Chapters", [])
        done = total - len(failed)
        console.print(
            Panel(
                f"[bold green]Completed:[/] {done}/{total} chapters\n"
                f"[{'bold red' if failed else 'dim'}]Failed:[/] {failed or 'none'}",
                title="[bold cyan]Download Summary[/]",
                border_style="green" if not failed else "yellow",
            )
        )

    finally:
        await downloader.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Preflight check
# ─────────────────────────────────────────────────────────────────────────────


async def run_preflight(downloader: MangaDownloader, csv_path: str) -> bool:
    await downloader.init_session(enable_browser=False)
    all_ok = True
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("Manga Name") or "").strip() or "Unknown"
                link = (row.get("Manga Link") or "").strip()
                if not link:
                    continue
                url = link.split("/chapter-")[0]
                result = await _preflight_check(downloader, url)
                state = "[bold green]OK[/]" if result["ok"] else "[bold red]BLOCKED[/]"
                console.print(f"  {state} {name} — status={result.get('status')} reason={result.get('reason')}")
                if not result["ok"]:
                    all_ok = False
    finally:
        await downloader.close()
    return all_ok


async def _preflight_check(downloader: MangaDownloader, url: str) -> Dict:
    try:
        async with downloader._get(
            url, headers=downloader._nato_headers(),
            timeout=downloader.config.download_timeout,
        ) as resp:
            body = (await resp.read())[:8192].decode("utf-8", errors="ignore").lower()
            if resp.status == 429:
                return {"ok": False, "status": resp.status, "reason": "rate_limited"}
            if resp.status == 403:
                reason = "cloudflare_block" if "cloudflare" in body else "forbidden"
                return {"ok": False, "status": 403, "reason": reason}
            if resp.status >= 400:
                return {"ok": False, "status": resp.status, "reason": f"http_{resp.status}"}
            return {"ok": True, "status": resp.status, "reason": "ok"}
    except Exception as e:
        return {"ok": False, "status": None, "reason": "network_error", "details": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manga-dl",
        description=(
            "All-in-one Manga Downloader  ·  Sources: NatoManga, AsuraScan\n\n"
            "Examples:\n"
            "  python manga_downloader_single.py --interactive\n"
            "  python manga_downloader_single.py --interactive --source asura\n"
            "  python manga_downloader_single.py manga_links.csv\n"
            "  python manga_downloader_single.py --interactive --browser-mode\n"
            "  python manga_downloader_single.py --interactive --browser-cdp-url http://127.0.0.1:9222"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    p.add_argument("csv_path", nargs="?", help="CSV file with 'Manga Name' and 'Manga Link' columns.")
    p.add_argument("--interactive", action="store_true", help="Interactive mode: search, select, download.")
    p.add_argument(
        "--source", choices=["natomanga", "asura"], default="natomanga",
        help="Source site: natomanga (default) or asura (AsuraScan)."
    )
    p.add_argument("--start-chapter", type=float, default=None, help="Chapter number to start from.")
    p.add_argument("--max-chapters", type=float, default=None, help="Maximum chapter number to download.")
    p.add_argument("--workers", type=int, default=3, help="Concurrent download workers (default: 3).")
    p.add_argument("--retry-failed", action="store_true", help="Retry previously failed downloads.")
    p.add_argument("--backup-interval", type=int, default=5, help="Backup every N chapters (default: 5).")
    p.add_argument("--proxy-list", type=str, help="Path to a text file containing proxy URLs.")
    p.add_argument("--header", action="append", default=[], help="Extra HTTP header: 'Key: Value'. Repeatable.")
    p.add_argument("--cookie", action="append", default=[], help="Extra cookie: 'name=value'. Repeatable.")
    p.add_argument("--preflight", action="store_true", help="Check connectivity before downloading.")
    p.add_argument("--preflight-only", action="store_true", help="Run preflight checks and exit.")
    p.add_argument("--browser-mode", action="store_true", help="Use Playwright browser for scraping (NatoManga).")
    p.add_argument("--browser-headless", action="store_true", help="Run Playwright in headless mode.")
    p.add_argument("--browser-timeout", type=int, default=30, help="Playwright navigation timeout in seconds.")
    p.add_argument("--browser-wait-for-challenge", action="store_true", help="Pause for manual challenge solve.")
    p.add_argument("--browser-profile-dir", default=".playwright-profile", help="Playwright persistent profile dir.")
    p.add_argument("--browser-cdp-url", default="", help="Attach to existing Chrome via CDP URL.")
    return p


def _parse_kv(items: List[str], sep: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in items:
        if sep not in item:
            continue
        k, v = item.split(sep, 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def main():
    args = build_parser().parse_args()

    if not args.csv_path and not args.interactive:
        console.print("[bold red]Error:[/] Provide <csv_path> or use --interactive")
        build_parser().print_help()
        return

    proxy_list: List[str] = []
    if args.proxy_list and Path(args.proxy_list).exists():
        proxy_list = [line.strip() for line in Path(args.proxy_list).read_text().splitlines() if line.strip()]

    # Auto-enable browser_mode when a CDP URL is provided
    browser_mode = args.browser_mode or bool(args.browser_cdp_url)

    config = Config(
        max_workers=args.workers,
        auto_retry_failed=args.retry_failed,
        backup_interval=args.backup_interval,
        proxy_list=proxy_list,
        request_headers=_parse_kv(args.header, ":"),
        cookies=_parse_kv(args.cookie, "="),
        browser_mode=browser_mode,
        browser_headless=args.browser_headless,
        browser_timeout=args.browser_timeout,
        browser_wait_for_challenge=args.browser_wait_for_challenge,
        browser_profile_dir=args.browser_profile_dir,
        browser_cdp_url=args.browser_cdp_url,
        source=args.source,
    )

    downloader = MangaDownloader(config=config, csv_path=args.csv_path or "manga_links.csv")

    if args.interactive:
        try:
            asyncio.run(interactive_mode(downloader, source=args.source))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interactive session interrupted.[/]")
        except Exception as e:
            console.print(f"\n[bold red]Error:[/] {e}")
            raise
        return

    if args.preflight_only:
        asyncio.run(run_preflight(downloader, args.csv_path))
        return

    if args.preflight:
        asyncio.run(run_preflight(downloader, args.csv_path))

    try:
        asyncio.run(downloader.run(args.start_chapter, args.max_chapters))
    except KeyboardInterrupt:
        console.print("\n[yellow]Download interrupted.[/]")
    except DownloadError as e:
        console.print(f"\n[bold red]Download error:[/] {e}")
    except Exception as e:
        console.print(f"\n[bold red]Unexpected error:[/] {e}")
        raise


if __name__ == "__main__":
    main()
