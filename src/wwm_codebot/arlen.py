from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from .bahamut import extract_codes_from_text, normalize_code
from .models import CodeSnapshot, CodeStatus, RedeemCode

ACTIVE_SECTION_MARKER = "✅ 有效兌換碼"
EXPIRED_SECTION_MARKER = "❌ 失效兌換碼"
BROWSER_ATTEMPTS = 3


def parse_arlen_codes(html: str, source_url: str) -> CodeSnapshot:
    text = _extract_page_text(html)
    active_lines = _extract_section_lines(
        text=text,
        start_marker=ACTIVE_SECTION_MARKER,
        end_marker=EXPIRED_SECTION_MARKER,
    )
    expired_lines = _extract_section_lines(
        text=text,
        start_marker=EXPIRED_SECTION_MARKER,
        end_marker=None,
    )

    collected: dict[str, RedeemCode] = {}
    order: list[str] = []

    for status, lines in (
        (CodeStatus.ACTIVE, active_lines),
        (CodeStatus.EXPIRED, expired_lines),
    ):
        for line in lines:
            for code in extract_codes_from_text(line):
                normalized = normalize_code(code)
                existing = collected.get(normalized)
                note = line if line != normalized else None
                candidate = RedeemCode(code=normalized, status=status, note=note)
                if existing is None:
                    collected[normalized] = candidate
                    order.append(normalized)
                    continue
                if existing.status == CodeStatus.ACTIVE and status == CodeStatus.EXPIRED:
                    collected[normalized] = candidate

    if not collected:
        raise ValueError("Could not find any redeem codes in Arlen source.")

    return CodeSnapshot(
        source_url=source_url,
        observed_at=datetime.now(timezone.utc),
        codes=[collected[code] for code in order],
    )


class ArlenCodesMonitor:
    def __init__(self, source_url: str, timeout_seconds: int = 20) -> None:
        self.source_url = source_url
        self.timeout_seconds = timeout_seconds

    async def fetch_snapshot(self) -> CodeSnapshot:
        try:
            html = await self._fetch_html_with_httpx()
        except (httpx.HTTPStatusError, RuntimeError, ValueError) as exc:
            print(
                "Arlen source httpx fetch failed, retrying with browser: "
                f"{type(exc).__name__} {exc}",
                flush=True,
            )
            html = await self._fetch_html_with_browser()
        return parse_arlen_codes(html, self.source_url)

    def _build_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Referer": "https://www.arlenfuture.com/",
        }

    async def _fetch_html_with_httpx(self) -> str:
        headers = self._build_headers()
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=headers,
            follow_redirects=True,
            http2=False,
        ) as client:
            response = await client.get(self.source_url)
            response.raise_for_status()
        return _ensure_arlen_html(response.text, source="httpx")

    async def _fetch_html_with_browser(self) -> str:
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is not installed in the runtime environment.") from exc

        headers = self._build_headers()
        timeout_ms = self.timeout_seconds * 1000

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
                            timeout_ms=timeout_ms,
                            attempt=attempt,
                        )
                        return html
                    except (PlaywrightTimeoutError, RuntimeError, ValueError) as exc:
                        last_error = exc
                        print(
                            "Arlen source browser attempt failed: "
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
        timeout_ms: int,
        attempt: int,
    ) -> str:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        await page.goto(
            self.source_url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(2000)

        for phase in ("initial", "reload"):
            html = await page.content()
            try:
                return _ensure_arlen_html(
                    html,
                    source=f"browser:{phase}:attempt={attempt}",
                )
            except RuntimeError as exc:
                if phase == "reload":
                    raise exc
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
                    html = await page.content()
                    return _ensure_arlen_html(
                        html,
                        source=f"browser:networkidle:attempt={attempt}",
                    )
                except (PlaywrightTimeoutError, RuntimeError):
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_timeout(2500)


def _extract_page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)


def _extract_section_lines(
    *,
    text: str,
    start_marker: str,
    end_marker: str | None,
) -> list[str]:
    start_index = text.find(start_marker)
    if start_index < 0:
        raise ValueError(f"Missing section marker: {start_marker}")

    section = text[start_index + len(start_marker) :]
    if end_marker is not None:
        end_index = section.find(end_marker)
        if end_index < 0:
            raise ValueError(f"Missing section marker: {end_marker}")
        section = section[:end_index]

    return [line.strip() for line in section.splitlines() if line.strip()]


def _ensure_arlen_html(html: str, *, source: str) -> str:
    text = _extract_page_text(html)
    if ACTIVE_SECTION_MARKER in text and EXPIRED_SECTION_MARKER in text:
        return html

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    raise RuntimeError(
        f"{source} did not reach Arlen codes content "
        f"(title={title!r}, missing markers)."
    )
