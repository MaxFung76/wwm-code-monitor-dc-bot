from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


# 兌換碼狀態
class CodeStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


# 單筆兌換碼（note 只留一點上下文）
@dataclass(slots=True)
class RedeemCode:
    code: str
    status: CodeStatus
    note: str | None = None


# 一次抓取的快照
@dataclass(slots=True)
class CodeSnapshot:
    source_url: str
    observed_at: datetime
    codes: list[RedeemCode]


# reconcile 的差異
@dataclass(slots=True)
class ReconcileResult:
    new_active_codes: list[RedeemCode]
    first_seen_active_codes: list[RedeemCode]
    changed_codes: list[RedeemCode]
