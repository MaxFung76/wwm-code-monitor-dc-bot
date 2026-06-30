from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .models import CodeSnapshot, CodeStatus, RedeemCode


def snapshot_to_dict(snapshot: CodeSnapshot) -> dict[str, Any]:
    return {
        "source_url": snapshot.source_url,
        "observed_at": snapshot.observed_at.isoformat(),
        "codes": [
            {
                "code": item.code,
                "status": item.status.value,
                "note": item.note,
            }
            for item in snapshot.codes
        ],
    }


def snapshot_from_dict(data: dict[str, Any]) -> CodeSnapshot:
    return CodeSnapshot(
        source_url=str(data["source_url"]),
        observed_at=datetime.fromisoformat(str(data["observed_at"])),
        codes=[
            RedeemCode(
                code=str(item["code"]),
                status=CodeStatus(str(item["status"])),
                note=None if item.get("note") is None else str(item["note"]),
            )
            for item in data.get("codes", [])
        ],
    )


def snapshot_to_json(snapshot: CodeSnapshot) -> str:
    return json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, indent=2)


def snapshot_from_json(payload: str) -> CodeSnapshot:
    return snapshot_from_dict(json.loads(payload))
