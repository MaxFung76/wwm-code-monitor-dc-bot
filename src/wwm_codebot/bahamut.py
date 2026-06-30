from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from .models import CodeSnapshot, CodeStatus, RedeemCode

CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{6,24}(?![A-Za-z0-9])")
EXPIRED_TAGS = {"strike", "s", "del"}
ARTICLE_SELECTORS = (
    ".c-article__content",
    ".c-post__body",
    "#article-content",
    "[itemprop='articleBody']",
)
MAINTENANCE_MARKERS = (
    "系統維修中",
    "維護中",
    "service unavailable",
)
BROWSER_ATTEMPTS = 3


def extract_codes_from_text(text: str) -> list[str]:
    tokens = CODE_PATTERN.findall(text.replace("\xa0", " "))
    seen: set[str] = set()
    codes: list[str] = []
    for token in tokens:
        if not any(ch.isalpha() for ch in token):
            continue
        if token not in seen:
            seen.add(token)
            codes.append(token)
    return codes


def parse_bahamut_codes(html: str, source_url: str) -> CodeSnapshot:
    soup = BeautifulSoup(html, "html.parser")
    article_root = _find_article_root(soup)

    collected: dict[str, RedeemCode] = {}

    for node in article_root.descendants:
        if not isinstance(node, NavigableString):
            continue
        if _should_skip_text_node(node):
            continue

        codes = extract_codes_from_text(str(node))
        if not codes:
            continue

        status = CodeStatus.EXPIRED if _is_expired(node) else CodeStatus.ACTIVE
        for code in codes:
            existing = collected.get(code)
            if existing is None:
                collected[code] = RedeemCode(code=code, status=status, note=str(node).strip())
                continue

            if existing.status == CodeStatus.ACTIVE and status == CodeStatus.EXPIRED:
                collected[code] = RedeemCode(code=code, status=status, note=str(node).strip())

    return CodeSnapshot(
        source_url=source_url,
        observed_at=datetime.now(timezone.utc),
        codes=list(collected.values()),
    )


class BahamutMonitor:
    def __init__(self, forum_url: str, timeout_seconds: int = 20) -> None:
        self.forum_url = forum_url
        self.timeout_seconds = timeout_seconds

    async def fetch_snapshot(self) -> CodeSnapshot:
        try:
            html = await self._fetch_html_with_httpx()
        except (httpx.HTTPStatusError, RuntimeError) as exc:
            print(
                "Bahamut httpx fetch failed, retrying with browser: "
                f"{type(exc).__name__} {exc}",
                flush=True,
            )
            html = await self._fetch_html_with_browser()
        return parse_bahamut_codes(html, self.forum_url)

    def _build_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Referer": "https://forum.gamer.com.tw/",
            "Origin": "https://forum.gamer.com.tw",
        }

    async def _fetch_html_with_httpx(self) -> str:
        headers = self._build_headers()
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=headers,
            follow_redirects=True,
            http2=False,
        ) as client:
            response = await client.get(self.forum_url)
            response.raise_for_status()
        return _ensure_article_html(response.text, source="httpx")

    async def _fetch_html_with_browser(self) -> str:
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is not installed in the runtime environment.") from exc

        headers = self._build_headers()
        timeout_ms = self.timeout_seconds * 1000
        selector = ARTICLE_SELECTORS[0]

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=headers["User-Agent"],
                locale="zh-TW",
                extra_http_headers={
                    "Accept": headers["Accept"],
                    "Accept-Language": headers["Accept-Language"],
                    "Referer": headers["Referer"],
                },
                viewport={"width": 1440, "height": 900},
            )
            try:
                await context.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', {
                      get: () => undefined,
                    });
                    """
                )
                last_error: Exception | None = None
                for attempt in range(1, BROWSER_ATTEMPTS + 1):
                    page = await context.new_page()
                    try:
                        html = await self._fetch_browser_attempt(
                            page=page,
                            selector=selector,
                            timeout_ms=timeout_ms,
                            attempt=attempt,
                        )
                        return html
                    except (PlaywrightTimeoutError, RuntimeError) as exc:
                        last_error = exc
                        print(
                            "Bahamut browser attempt failed: "
                            f"attempt={attempt}/{BROWSER_ATTEMPTS} "
                            f"{type(exc).__name__} {exc}",
                            flush=True,
                        )
                        if attempt < BROWSER_ATTEMPTS:
                            await asyncio.sleep(min(2 * attempt, 5))
                    finally:
                        await page.close()
                if last_error is not None:
                    raise RuntimeError(f"browser fetch failed after retries: {last_error}") from last_error
                raise RuntimeError("browser fetch failed without a captured exception.")
            finally:
                await context.close()
                await browser.close()

    async def _fetch_browser_attempt(
        self,
        *,
        page,
        selector: str,
        timeout_ms: int,
        attempt: int,
    ) -> str:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        await page.goto(
            self.forum_url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(1500)

        # Bahamut occasionally returns a maintenance/interstitial page first.
        # Reload once inside the same attempt before declaring failure.
        for phase in ("initial", "reload"):
            html = await page.content()
            try:
                return _ensure_article_html(
                    html,
                    source=f"browser:{phase}:attempt={attempt}",
                )
            except RuntimeError as exc:
                if phase == "reload":
                    raise exc
                try:
                    await page.locator(selector).first.wait_for(
                        state="attached",
                        timeout=min(timeout_ms, 4000),
                    )
                    html = await page.content()
                    return _ensure_article_html(
                        html,
                        source=f"browser:selector:attempt={attempt}",
                    )
                except (PlaywrightTimeoutError, RuntimeError):
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_timeout(2000)


def _find_article_root(soup: BeautifulSoup) -> Tag:
    for selector in ARTICLE_SELECTORS:
        tag = soup.select_one(selector)
        if tag:
            return tag
    raise ValueError("Could not find Bahamut article content root.")


def _should_skip_text_node(node: NavigableString) -> bool:
    parent = node.parent
    if parent is None:
        return True
    if parent.name in {"script", "style"}:
        return True
    return not str(node).strip()


def _is_expired(node: NavigableString) -> bool:
    return any(ancestor.name in EXPIRED_TAGS for ancestor in _iter_ancestors(node))


def _iter_ancestors(node: NavigableString) -> Iterable[Tag]:
    parent = node.parent
    while isinstance(parent, Tag):
        yield parent
        parent = parent.parent


def _ensure_article_html(html: str, *, source: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title_lower = title.lower()
    if any(marker in title_lower for marker in MAINTENANCE_MARKERS):
        raise RuntimeError(f"{source} received maintenance page (title={title!r}).")

    body_text = soup.get_text(" ", strip=True)
    if any(marker in body_text.lower() for marker in MAINTENANCE_MARKERS):
        raise RuntimeError(f"{source} received maintenance content.")

    for selector in ARTICLE_SELECTORS:
        if soup.select_one(selector):
            return html

    raise RuntimeError(f"{source} did not reach Bahamut article content (title={title!r}).")
