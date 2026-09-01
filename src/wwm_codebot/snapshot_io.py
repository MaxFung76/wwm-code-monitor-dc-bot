from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .bahamut import normalize_code
from .models import CodeSnapshot, CodeStatus, RedeemCode


def snapshot_to_dict(snapshot: CodeSnapshot) -> dict[str, Any]:
    # 將內部模型轉成穩定 JSON schema（供 REMOTE_SNAPSHOT_URL / debug 使用）
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
    # 反序列化時統一做代碼正規化（大寫），避免重複公告
    return CodeSnapshot(
        source_url=str(data["source_url"]),
        observed_at=datetime.fromisoformat(str(data["observed_at"])),
        codes=[
            RedeemCode(
                code=normalize_code(str(item["code"])),
                status=CodeStatus(str(item["status"])),
                note=None if item.get("note") is None else str(item["note"]),
            )
            for item in data.get("codes", [])
        ],
    )


def snapshot_to_json(snapshot: CodeSnapshot) -> str:
    # 讓輸出可讀，方便直接在 GitHub 上檢視 snapshot-cache 檔案
    return json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, indent=2)


def snapshot_from_json(payload: str) -> CodeSnapshot:
    # REMOTE_SNAPSHOT_URL 會走這裡解析
    return snapshot_from_dict(json.loads(payload))
