from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .bahamut import is_probable_code
from .models import CodeStatus, ReconcileResult, RedeemCode


@dataclass(slots=True)
class MonthlyRow:
    code: str
    status: str
    first_seen_at: str
    source_type: str


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def reconcile_codes(
        self,
        codes: list[RedeemCode],
        *,
        source_url: str,
        source_type: str,
    ) -> ReconcileResult:
        return await asyncio.to_thread(
            self._reconcile_codes,
            codes,
            source_url,
            source_type,
        )

    async def get_state(self, key: str) -> str | None:
        return await asyncio.to_thread(self._get_state, key)

    async def set_state(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._set_state, key, value)

    async def get_monthly_rows(self, now: datetime | None = None) -> list[MonthlyRow]:
        return await asyncio.to_thread(self._get_monthly_rows, now)

    async def get_code_status(self, code: str) -> tuple[str, str] | None:
        return await asyncio.to_thread(self._get_code_status, code)

    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS redeem_codes (
                    code TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    note TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_status_change_at TEXT NOT NULL,
                    last_announced_at TEXT
                );

                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    note TEXT,
                    observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._delete_invalid_codes(conn)

    def _reconcile_codes(
        self,
        codes: list[RedeemCode],
        source_url: str,
        source_type: str,
    ) -> ReconcileResult:
        now = datetime.now(timezone.utc).isoformat()
        deduped = {
            item.code: item
            for item in codes
            if is_probable_code(item.code)
        }
        new_active_codes: list[RedeemCode] = []
        changed_codes: list[RedeemCode] = []

        with self._connect() as conn:
            for item in deduped.values():
                row = conn.execute(
                    """
                    SELECT status
                    FROM redeem_codes
                    WHERE code = ?
                    """,
                    (item.code,),
                ).fetchone()

                conn.execute(
                    """
                    INSERT INTO observations(code, status, source_url, source_type, note, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item.code, item.status.value, source_url, source_type, item.note, now),
                )

                if row is None:
                    conn.execute(
                        """
                        INSERT INTO redeem_codes(
                            code, status, source_url, source_type, note,
                            first_seen_at, last_seen_at, last_status_change_at, last_announced_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.code,
                            item.status.value,
                            source_url,
                            source_type,
                            item.note,
                            now,
                            now,
                            now,
                            now if item.status == CodeStatus.ACTIVE else None,
                        ),
                    )
                    changed_codes.append(item)
                    if item.status == CodeStatus.ACTIVE:
                        new_active_codes.append(item)
                    continue

                previous_status = CodeStatus(row["status"])
                has_changed = previous_status != item.status
                conn.execute(
                    """
                    UPDATE redeem_codes
                    SET status = ?,
                        source_url = ?,
                        source_type = ?,
                        note = ?,
                        last_seen_at = ?,
                        last_status_change_at = CASE
                            WHEN status <> ? THEN ?
                            ELSE last_status_change_at
                        END,
                        last_announced_at = CASE
                            WHEN status <> ? AND ? = 'active' THEN ?
                            ELSE last_announced_at
                        END
                    WHERE code = ?
                    """,
                    (
                        item.status.value,
                        source_url,
                        source_type,
                        item.note,
                        now,
                        item.status.value,
                        now,
                        item.status.value,
                        item.status.value,
                        now,
                        item.code,
                    ),
                )

                if has_changed:
                    changed_codes.append(item)
                if item.status == CodeStatus.ACTIVE and previous_status != CodeStatus.ACTIVE:
                    new_active_codes.append(item)

        return ReconcileResult(new_active_codes=new_active_codes, changed_codes=changed_codes)

    def _get_state(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_state WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row["value"])

    def _set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_state(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def _get_monthly_rows(self, now: datetime | None = None) -> list[MonthlyRow]:
        current = now or datetime.now(timezone.utc)
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT code, status, first_seen_at, source_type
                FROM redeem_codes
                WHERE first_seen_at >= ?
                ORDER BY first_seen_at DESC
                """,
                (month_start.isoformat(),),
            ).fetchall()
        return [MonthlyRow(**dict(row)) for row in rows]

    def _get_code_status(self, code: str) -> tuple[str, str] | None:
        if not is_probable_code(code):
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status, source_type
                FROM redeem_codes
                WHERE code = ?
                """,
                (code,),
            ).fetchone()
        if row is None:
            return None
        return (str(row["status"]), str(row["source_type"]))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _delete_invalid_codes(self, conn: sqlite3.Connection) -> None:
        invalid_codes = [
            str(row["code"])
            for row in conn.execute("SELECT code FROM redeem_codes").fetchall()
            if not is_probable_code(str(row["code"]))
        ]
        if not invalid_codes:
            return

        placeholders = ", ".join("?" for _ in invalid_codes)
        conn.execute(
            f"DELETE FROM redeem_codes WHERE code IN ({placeholders})",
            invalid_codes,
        )
        conn.execute(
            f"DELETE FROM observations WHERE code IN ({placeholders})",
            invalid_codes,
        )
