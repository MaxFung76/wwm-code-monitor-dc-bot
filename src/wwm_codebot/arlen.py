from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from .bahamut import extract_codes_from_text, normalize_code
from .models import CodeSnapshot, CodeStatus, RedeemCode

# 以頁面文字標記切出「有效」與「失效」區塊，避免靠不穩定的 class name
ACTIVE_SECTION_MARKER = "✅ 有效兌換碼"
EXPIRED_SECTION_MARKER = "❌ 失效兌換碼"
# Playwright 重試次數：遇到攔截/半載入時降低失敗率
BROWSER_ATTEMPTS = 3


def parse_arlen_codes(html: str, source_url: str) -> CodeSnapshot:
    # 解析阿冷整理頁：把頁面轉成純文字後，依標記切區段，再用既有 code regex 抽碼
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

    # 同碼若同時出現在有效/失效區，失效優先（避免誤公告）
    for status, lines in (
        (CodeStatus.ACTIVE, active_lines),
        (CodeStatus.EXPIRED, expired_lines),
    ):
        for line in lines:
            for code in extract_codes_from_text(line):
                normalized = normalize_code(code)
                existing = collected.get(normalized)
                # note 用來保留原行文字（常含到期資訊/備註），但避免 note = code 本身
                note = line if line != normalized else None
                candidate = RedeemCode(code=normalized, status=status, note=note)
                if existing is None:
                    collected[normalized] = candidate
                    order.append(normalized)
                    continue
                if existing.status == CodeStatus.ACTIVE and status == CodeStatus.EXPIRED:
                    collected[normalized] = candidate

    if not collected:
        # 缺 marker 或頁面被攔截時，走到這裡會提醒呼叫端嘗試 browser fallback
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
        # 先用 httpx（成本較低），失敗再用 Playwright（成本較高但較能穿透攔截/跳轉）
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
        # 用較像真實瀏覽器的 header，降低被擋或回傳簡化頁的機率
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
        # httpx 路徑：快速抓取 HTML（若遇到跳轉/攔截，_ensure_arlen_html 會 fail-fast）
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
        # Playwright 路徑：用瀏覽器載入，處理動態內容或 Cloudflare 類型的中介頁
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
                    # 同一個 context 內重試：降低冷啟與反爬造成的變異
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

        # 先等 domcontentloaded，再給緩衝時間讓內容掛載
        await page.goto(
            self.source_url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(2000)

        # 同一 attempt 內做一次 reload 檢查，避免卡在中介頁或未完成載入
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
                    # 盡量等到 networkidle，再取一次內容，提升拿到 marker 的機率
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
                    html = await page.content()
                    return _ensure_arlen_html(
                        html,
                        source=f"browser:networkidle:attempt={attempt}",
                    )
                except (PlaywrightTimeoutError, RuntimeError):
                    # networkidle 等不到就 reload，避免停在半載入狀態
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_timeout(2500)


def _extract_page_text(html: str) -> str:
    # 將 HTML 轉成純文字；用換行保留段落，便於後續以 marker 切段
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)


def _extract_section_lines(
    *,
    text: str,
    start_marker: str,
    end_marker: str | None,
) -> list[str]:
    # 以 marker 切出區段，回傳非空行列表
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
    # 確保頁面包含有效/失效 marker；缺少通常代表被中介頁/攔截頁替換
    text = _extract_page_text(html)
    if ACTIVE_SECTION_MARKER in text and EXPIRED_SECTION_MARKER in text:
        return html

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    raise RuntimeError(
        f"{source} did not reach Arlen codes content "
        f"(title={title!r}, missing markers)."
    )
