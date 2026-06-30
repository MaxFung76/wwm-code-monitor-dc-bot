from __future__ import annotations

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
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=headers,
            follow_redirects=True,
            http2=False,
        ) as client:
            response = await client.get(self.forum_url)
            response.raise_for_status()
        return parse_bahamut_codes(response.text, self.forum_url)


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
