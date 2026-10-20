from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .code_utils import normalize_code
from .models import CodeSnapshot, CodeStatus, RedeemCode


DEFAULT_API_URL = "https://codes-backend.wwmcodes.workers.dev/v1/codes"


def parse_yar_codes(data: dict[str, Any], source_url: str) -> CodeSnapshot:
    updated_at = data.get("updatedAt")
    observed_at = datetime.now(timezone.utc)
    if isinstance(updated_at, str):
        try:
            observed_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    collected: dict[str, RedeemCode] = {}

    for item in data.get("active", []):
        if not isinstance(item, dict):
            continue
        raw = item.get("code")
        if not isinstance(raw, str):
            continue
        code = normalize_code(raw)
        collected[code] = RedeemCode(code=code, status=CodeStatus.ACTIVE, note=None)

    for item in data.get("expired", []):
        if not isinstance(item, dict):
            continue
        raw = item.get("code")
        if not isinstance(raw, str):
            continue
        code = normalize_code(raw)
        existing = collected.get(code)
        if existing is None or existing.status == CodeStatus.ACTIVE:
            collected[code] = RedeemCode(code=code, status=CodeStatus.EXPIRED, note=None)

    return CodeSnapshot(
        source_url=source_url,
        observed_at=observed_at,
        codes=list(collected.values()),
    )


class YarCodesMonitor:
    def __init__(self, api_url: str = DEFAULT_API_URL, timeout_seconds: int = 20) -> None:
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds

    async def fetch_snapshot(self) -> CodeSnapshot:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
            response = await client.get(self.api_url, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("codes API returned a non-object payload.")
        return parse_yar_codes(payload, self.api_url)

