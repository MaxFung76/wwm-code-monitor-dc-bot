from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .bahamut import is_probable_code, normalize_code
from .models import CodeStatus, ReconcileResult, RedeemCode


@dataclass(slots=True)
class MonthlyRow:
    # 月報顯示用的扁平資料結構（避免 UI 端再做 dict 操作）
    code: str
    status: str
    first_seen_at: str
    source_type: str


class Storage:
    def __init__(self, database_path: Path) -> None:
        # 單機 SQLite：資料量不大時足夠，且方便 Docker volume 持久化
        self.database_path = database_path

    async def initialize(self) -> None:
        # 初始化在 background thread 執行，避免阻塞 discord event loop
        await asyncio.to_thread(self._initialize)

    async def reconcile_codes(
        self,
        codes: list[RedeemCode],
        *,
        source_url: str,
        source_type: str,
        record_observations: bool = True,
        update_redeem_codes: bool = True,
    ) -> ReconcileResult:
        # 將一批 codes 寫入資料庫並回傳差異（new_active_codes 用於公告）
        return await asyncio.to_thread(
            self._reconcile_codes,
            codes,
            source_url,
            source_type,
            record_observations,
            update_redeem_codes,
        )

    async def record_observations(
        self,
        codes: list[RedeemCode],
        *,
        source_url: str,
        source_type: str,
    ) -> None:
        await asyncio.to_thread(
            self._record_observations,
            codes,
            source_url,
            source_type,
        )

    async def get_status_map(self, codes: list[str]) -> dict[str, CodeStatus]:
        return await asyncio.to_thread(self._get_status_map, codes)

    async def get_state(self, key: str) -> str | None:
        # bot_state：存面板訊息 id / 面板頻道 id 等輕量狀態
        return await asyncio.to_thread(self._get_state, key)

    async def set_state(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._set_state, key, value)

    async def get_monthly_rows(self, now: datetime | None = None) -> list[MonthlyRow]:
        # 月報（不含已讀判斷）：回傳本月首次出現的全部代碼
        return await asyncio.to_thread(self._get_monthly_rows, now)

    async def get_unseen_monthly_rows(
        self,
        user_id: int,
        now: datetime | None = None,
    ) -> list[MonthlyRow]:
        # 月報（含已讀判斷）：只回傳 user 尚未看過、且仍為 active 的代碼
        return await asyncio.to_thread(self._get_unseen_monthly_rows, user_id, now)

    async def mark_codes_seen(
        self,
        user_id: int,
        codes: list[str],
        seen_at: datetime | None = None,
    ) -> None:
        # 標記使用者已讀：同碼若重新變成 active，會以 last_status_change_at 觸發重新顯示
        await asyncio.to_thread(self._mark_codes_seen, user_id, codes, seen_at)

    async def get_code_status(self, code: str) -> tuple[str, str] | None:
        # 供 /sync_now 查核：查詢該 code 目前在 DB 的狀態與來源
        return await asyncio.to_thread(self._get_code_status, code)

    def _initialize(self) -> None:
        # schema 建立 + 啟動時資料修補（移除純數字碼、合併大小寫變體）
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

                CREATE TABLE IF NOT EXISTS user_code_views (
                    user_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, code)
                );
                """
            )
            self._delete_invalid_codes(conn)
            self._normalize_existing_codes(conn)

    def _reconcile_codes(
        self,
        codes: list[RedeemCode],
        source_url: str,
        source_type: str,
        record_observations: bool,
        update_redeem_codes: bool,
    ) -> ReconcileResult:
        # reconcile 核心：
        # - 將輸入 codes 正規化去重（大寫）
        # - 永遠寫一筆 observations（作為歷史紀錄）
        # - 若 status 變化，更新 last_status_change_at 並視情況更新 last_announced_at
        now = datetime.now(timezone.utc).isoformat()
        deduped = {
            normalize_code(item.code): RedeemCode(
                code=normalize_code(item.code),
                status=item.status,
                note=item.note,
            )
            for item in codes
            if is_probable_code(item.code)
        }
        new_active_codes: list[RedeemCode] = []
        first_seen_active_codes: list[RedeemCode] = []
        changed_codes: list[RedeemCode] = []

        with self._connect() as conn:
            for item in deduped.values():
                row = None
                if update_redeem_codes:
                    row = conn.execute(
                        """
                        SELECT status
                        FROM redeem_codes
                        WHERE code = ?
                        """,
                        (item.code,),
                    ).fetchone()

                if record_observations:
                    conn.execute(
                        """
                        INSERT INTO observations(code, status, source_url, source_type, note, observed_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (item.code, item.status.value, source_url, source_type, item.note, now),
                    )

                if not update_redeem_codes:
                    continue

                if row is None:
                    if item.status != CodeStatus.ACTIVE:
                        continue
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
                    new_active_codes.append(item)
                    first_seen_active_codes.append(item)
                    continue

                previous_status = CodeStatus(row["status"])
                has_changed = previous_status != item.status

                if previous_status == CodeStatus.EXPIRED and item.status == CodeStatus.ACTIVE:
                    conn.execute(
                        """
                        UPDATE redeem_codes
                        SET source_url = ?,
                            source_type = ?,
                            note = ?,
                            last_seen_at = ?
                        WHERE code = ?
                        """,
                        (
                            source_url,
                            source_type,
                            item.note,
                            now,
                            item.code,
                        ),
                    )
                    continue

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

        return ReconcileResult(
            new_active_codes=new_active_codes,
            first_seen_active_codes=first_seen_active_codes,
            changed_codes=changed_codes,
        )

    def _record_observations(
        self,
        codes: list[RedeemCode],
        source_url: str,
        source_type: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        deduped = {
            normalize_code(item.code): RedeemCode(
                code=normalize_code(item.code),
                status=item.status,
                note=item.note,
            )
            for item in codes
            if is_probable_code(item.code)
        }
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO observations(code, status, source_url, source_type, note, observed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (item.code, item.status.value, source_url, source_type, item.note, now)
                    for item in deduped.values()
                ],
            )

    def _get_status_map(self, codes: list[str]) -> dict[str, CodeStatus]:
        normalized = [normalize_code(code) for code in codes if is_probable_code(code)]
        if not normalized:
            return {}
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in normalized)
            rows = conn.execute(
                f"SELECT code, status FROM redeem_codes WHERE code IN ({placeholders})",
                tuple(normalized),
            ).fetchall()
        return {str(row["code"]): CodeStatus(str(row["status"])) for row in rows}

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
        # 以 first_seen_at 作為「本月新碼」的判斷基準
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

    def _get_unseen_monthly_rows(
        self,
        user_id: int,
        now: datetime | None = None,
    ) -> list[MonthlyRow]:
        # user_code_views.seen_at < redeem_codes.last_status_change_at：
        # 代表使用者看到後該碼又重新變成 active，需要再次顯示
        current = now or datetime.now(timezone.utc)
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT redeem_codes.code, redeem_codes.status, redeem_codes.first_seen_at, redeem_codes.source_type
                FROM redeem_codes
                LEFT JOIN user_code_views
                    ON user_code_views.code = redeem_codes.code
                   AND user_code_views.user_id = ?
                WHERE redeem_codes.first_seen_at >= ?
                  AND redeem_codes.status = 'active'
                  AND (
                      user_code_views.seen_at IS NULL
                      OR user_code_views.seen_at < redeem_codes.last_status_change_at
                  )
                ORDER BY redeem_codes.first_seen_at DESC
                """,
                (user_id, month_start.isoformat()),
            ).fetchall()
        return [MonthlyRow(**dict(row)) for row in rows]

    def _mark_codes_seen(
        self,
        user_id: int,
        codes: list[str],
        seen_at: datetime | None = None,
    ) -> None:
        # 批次 upsert：同一 user+code 只保留最新 seen_at
        valid_codes = [normalize_code(code) for code in codes if is_probable_code(code)]
        if not valid_codes:
            return

        marked_at = (seen_at or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO user_code_views(user_id, code, seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, code) DO UPDATE SET seen_at = excluded.seen_at
                """,
                [(user_id, code, marked_at) for code in valid_codes],
            )

    def _get_code_status(self, code: str) -> tuple[str, str] | None:
        # 對外查詢前先做基本過濾與正規化，避免把純數字/雜訊當成 code
        if not is_probable_code(code):
            return None
        normalized = normalize_code(code)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status, source_type
                FROM redeem_codes
                WHERE code = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return (str(row["status"]), str(row["source_type"]))

    def _connect(self) -> sqlite3.Connection:
        # row_factory 設成 Row，便於用 row["field"] 取值
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _delete_invalid_codes(self, conn: sqlite3.Connection) -> None:
        # 清理純數字碼（常見為 Discord message id 或其他非兌換碼 token）
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
        conn.execute(
            f"DELETE FROM user_code_views WHERE code IN ({placeholders})",
            invalid_codes,
        )

    def _normalize_existing_codes(self, conn: sqlite3.Connection) -> None:
        # 歷史資料修補：把大小寫不同但同一組字元的 code 合併成同一筆（統一大寫）
        redeem_rows = conn.execute("SELECT * FROM redeem_codes").fetchall()
        merged_codes: dict[str, dict[str, str | None]] = {}
        for row in redeem_rows:
            normalized = normalize_code(str(row["code"]))
            current = merged_codes.get(normalized)
            candidate = dict(row)
            candidate["code"] = normalized
            if current is None:
                merged_codes[normalized] = candidate
                continue
            merged_codes[normalized] = self._merge_redeem_rows(current, candidate)

        conn.execute("DELETE FROM redeem_codes")
        conn.executemany(
            """
            INSERT INTO redeem_codes(
                code, status, source_url, source_type, note,
                first_seen_at, last_seen_at, last_status_change_at, last_announced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["code"],
                    row["status"],
                    row["source_url"],
                    row["source_type"],
                    row["note"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                    row["last_status_change_at"],
                    row["last_announced_at"],
                )
                for row in merged_codes.values()
            ],
        )

        observation_rows = conn.execute("SELECT * FROM observations").fetchall()
        conn.execute("DELETE FROM observations")
        conn.executemany(
            """
            INSERT INTO observations(code, status, source_url, source_type, note, observed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    normalize_code(str(row["code"])),
                    row["status"],
                    row["source_url"],
                    row["source_type"],
                    row["note"],
                    row["observed_at"],
                )
                for row in observation_rows
            ],
        )

        view_rows = conn.execute("SELECT * FROM user_code_views").fetchall()
        merged_views: dict[tuple[int, str], str] = {}
        for row in view_rows:
            key = (int(row["user_id"]), normalize_code(str(row["code"])))
            seen_at = str(row["seen_at"])
            existing = merged_views.get(key)
            if existing is None or seen_at > existing:
                merged_views[key] = seen_at

        conn.execute("DELETE FROM user_code_views")
        conn.executemany(
            """
            INSERT INTO user_code_views(user_id, code, seen_at)
            VALUES (?, ?, ?)
            """,
            [(user_id, code, seen_at) for (user_id, code), seen_at in merged_views.items()],
        )

    def _merge_redeem_rows(
        self,
        existing: dict[str, str | None],
        candidate: dict[str, str | None],
    ) -> dict[str, str | None]:
        # 合併策略：以 last_seen_at 較新的那筆作為 status/source 欄位來源，其餘時間欄位做 min/max
        preferred = self._pick_latest_by_timestamp((existing, candidate), "last_seen_at")
        return {
            "code": str(existing["code"]),
            "status": str(preferred["status"]),
            "source_url": str(preferred["source_url"]),
            "source_type": str(preferred["source_type"]),
            "note": preferred["note"],
            "first_seen_at": min(str(existing["first_seen_at"]), str(candidate["first_seen_at"])),
            "last_seen_at": max(str(existing["last_seen_at"]), str(candidate["last_seen_at"])),
            "last_status_change_at": max(
                str(existing["last_status_change_at"]),
                str(candidate["last_status_change_at"]),
            ),
            "last_announced_at": self._max_optional_timestamp(
                existing["last_announced_at"],
                candidate["last_announced_at"],
            ),
        }

    def _pick_latest_by_timestamp(
        self,
        rows: Iterable[dict[str, str | None]],
        field: str,
    ) -> dict[str, str | None]:
        # timestamp 皆以 ISO8601 字串存放，可直接用字串比較
        return max(rows, key=lambda row: str(row[field]))

    def _max_optional_timestamp(self, first: str | None, second: str | None) -> str | None:
        # 允許 None：以較晚者為準
        options = [value for value in (first, second) if value is not None]
        return max(options) if options else None
