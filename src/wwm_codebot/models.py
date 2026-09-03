from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


# 兌換碼狀態：ACTIVE 代表仍可使用；EXPIRED 代表已失效（或來源標記為失效）
class CodeStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


# 單筆兌換碼資料：note 用來存來源附近文字（例如到期資訊或原文片段）
@dataclass(slots=True)
class RedeemCode:
    code: str
    status: CodeStatus
    note: str | None = None


# 一次「同步」抓到的完整快照
@dataclass(slots=True)
class CodeSnapshot:
    source_url: str
    observed_at: datetime
    codes: list[RedeemCode]


# reconcile 後的差異：
# - new_active_codes：所有需要公告的 active（只包含首次出現為 active 的情境）
# - first_seen_active_codes：真正「首次出現」的 active（避免 partial snapshot 時洗頻）
# - changed_codes：供除錯/追蹤
@dataclass(slots=True)
class ReconcileResult:
    new_active_codes: list[RedeemCode]
    first_seen_active_codes: list[RedeemCode]
    changed_codes: list[RedeemCode]
