from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CodeStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


@dataclass(slots=True)
class RedeemCode:
    code: str
    status: CodeStatus
    note: str | None = None


@dataclass(slots=True)
class CodeSnapshot:
    source_url: str
    observed_at: datetime
    codes: list[RedeemCode]


@dataclass(slots=True)
class ReconcileResult:
    new_active_codes: list[RedeemCode]
    changed_codes: list[RedeemCode]
