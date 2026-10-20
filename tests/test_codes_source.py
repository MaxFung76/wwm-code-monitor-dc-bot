from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from wwm_codebot.code_utils import extract_codes_from_text
from wwm_codebot.discord_bot import (
    build_snapshot_candidate_urls,
    channel_matches_target,
    merge_snapshots,
    RedeemCodeBot,
)
from wwm_codebot.models import CodeSnapshot, CodeStatus, RedeemCode
from wwm_codebot.snapshot_io import snapshot_from_json, snapshot_to_json
from wwm_codebot.storage import Storage
from wwm_codebot.yar import DEFAULT_API_URL, parse_yar_codes


def test_parse_yar_codes_marks_active_and_expired() -> None:
    snapshot = parse_yar_codes(
        {
            "updatedAt": "2026-10-19T16:55:18.510Z",
            "active": [{"code": "abc123xy"}],
            "expired": [{"code": "ABC123XY"}, {"code": "EXPIRED77"}],
        },
        "https://example.com/api",
    )
    status_map = {item.code: item.status for item in snapshot.codes}
    assert status_map["ABC123XY"] == CodeStatus.EXPIRED
    assert status_map["EXPIRED77"] == CodeStatus.EXPIRED


def test_extract_codes_from_text_normalizes_and_dedupes() -> None:
    codes = extract_codes_from_text("abc123xy ABC123XY 1182577423678713917")
    assert codes == ["ABC123XY"]


def test_storage_only_notifies_new_active_codes(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio

    asyncio.run(storage.initialize())

    first = asyncio.run(
        storage.reconcile_codes(
            [],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    assert first.new_active_codes == []
    assert first.first_seen_active_codes == []

    second = asyncio.run(
        storage.reconcile_codes(
            [
                RedeemCode(code="NEWCODE88", status=CodeStatus.ACTIVE, note=None),
                RedeemCode(code="OLDCODE99", status=CodeStatus.EXPIRED, note=None),
            ],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    assert [item.code for item in second.new_active_codes] == ["NEWCODE88"]
    assert [item.code for item in second.first_seen_active_codes] == ["NEWCODE88"]

    third = asyncio.run(
        storage.reconcile_codes(
            [
                RedeemCode(code="NEWCODE88", status=CodeStatus.ACTIVE, note=None),
                RedeemCode(code="OLDCODE99", status=CodeStatus.EXPIRED, note=None),
            ],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    assert third.new_active_codes == []
    assert third.first_seen_active_codes == []


def test_snapshot_json_round_trip() -> None:
    snapshot = CodeSnapshot(
        source_url="https://example.com",
        observed_at=datetime.now(timezone.utc),
        codes=[
            RedeemCode(code="TESTCODE1", status=CodeStatus.ACTIVE, note="note"),
            RedeemCode(code="TESTCODE2", status=CodeStatus.EXPIRED, note=None),
        ],
    )

    restored = snapshot_from_json(snapshot_to_json(snapshot))

    assert restored.source_url == snapshot.source_url
    assert [(item.code, item.status, item.note) for item in restored.codes] == [
        ("TESTCODE1", CodeStatus.ACTIVE, "note"),
        ("TESTCODE2", CodeStatus.EXPIRED, None),
    ]


def test_storage_initialize_removes_numeric_only_codes(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio
    import sqlite3

    asyncio.run(storage.initialize())

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(storage.database_path)
    conn.execute(
        """
        INSERT INTO redeem_codes(
            code, status, source_url, source_type, note,
            first_seen_at, last_seen_at, last_status_change_at, last_announced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "1182577423678713917",
            "active",
            "https://example.com",
            "message",
            "numeric id",
            now,
            now,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO observations(code, status, source_url, source_type, note, observed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "1182577423678713917",
            "active",
            "https://example.com",
            "message",
            "numeric id",
            now,
        ),
    )
    conn.commit()
    conn.close()

    asyncio.run(storage.initialize())

    assert asyncio.run(storage.get_code_status("1182577423678713917")) is None


def test_storage_hides_seen_monthly_codes_per_user(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio

    asyncio.run(storage.initialize())

    asyncio.run(
        storage.reconcile_codes(
            [RedeemCode(code="UNSEEN123", status=CodeStatus.ACTIVE, note="first active")],
            source_url="https://example.com",
            source_type="monitor",
        )
    )

    first_rows = asyncio.run(storage.get_unseen_monthly_rows(user_id=1001))
    assert [row.code for row in first_rows] == ["UNSEEN123"]

    asyncio.run(storage.mark_codes_seen(user_id=1001, codes=["UNSEEN123"]))

    second_rows = asyncio.run(storage.get_unseen_monthly_rows(user_id=1001))
    assert second_rows == []

    other_user_rows = asyncio.run(storage.get_unseen_monthly_rows(user_id=2002))
    assert [row.code for row in other_user_rows] == ["UNSEEN123"]

    asyncio.run(
        storage.reconcile_codes(
            [RedeemCode(code="UNSEEN123", status=CodeStatus.EXPIRED, note="expired")],
            source_url="https://example.com",
            source_type="monitor",
        )
    )

    asyncio.run(
        storage.reconcile_codes(
            [RedeemCode(code="UNSEEN123", status=CodeStatus.ACTIVE, note="active again")],
            source_url="https://example.com",
            source_type="monitor",
        )
    )

    reactivated_rows = asyncio.run(storage.get_unseen_monthly_rows(user_id=1001))
    assert reactivated_rows == []
    assert asyncio.run(storage.get_code_status("UNSEEN123")) == (CodeStatus.EXPIRED.value, "monitor")


def test_storage_unmark_seen_restores_monthly_visibility(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio

    asyncio.run(storage.initialize())
    asyncio.run(
        storage.reconcile_codes(
            [RedeemCode(code="RESETSEEN1", status=CodeStatus.ACTIVE, note="active")],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    asyncio.run(storage.mark_codes_seen(user_id=1001, codes=["RESETSEEN1"]))
    assert asyncio.run(storage.get_unseen_monthly_rows(user_id=1001)) == []

    deleted = asyncio.run(storage.unmark_codes_seen(user_id=1001, codes=["RESETSEEN1"]))
    assert deleted == 1
    assert [row.code for row in asyncio.run(storage.get_unseen_monthly_rows(user_id=1001))] == [
        "RESETSEEN1"
    ]


def test_storage_treats_codes_case_insensitively(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio

    asyncio.run(storage.initialize())

    result = asyncio.run(
        storage.reconcile_codes(
            [
                RedeemCode(code="abc123xy", status=CodeStatus.ACTIVE, note="lower"),
                RedeemCode(code="ABC123XY", status=CodeStatus.ACTIVE, note="upper"),
            ],
            source_url="https://example.com",
            source_type="monitor",
        )
    )

    assert [item.code for item in result.new_active_codes] == ["ABC123XY"]
    assert asyncio.run(storage.get_code_status("abc123xy")) == ("active", "monitor")
    assert asyncio.run(storage.get_code_status("ABC123XY")) == ("active", "monitor")


def test_storage_initialize_merges_case_variant_codes(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio
    import sqlite3

    start = datetime.now(timezone.utc)
    lower_seen = start.isoformat()
    upper_seen = (start + timedelta(minutes=5)).isoformat()

    conn = sqlite3.connect(storage.database_path)
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
    conn.execute(
        """
        INSERT INTO redeem_codes(
            code, status, source_url, source_type, note,
            first_seen_at, last_seen_at, last_status_change_at, last_announced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "abc123xy",
            "active",
            "https://example.com/lower",
            "message",
            "lower",
            lower_seen,
            lower_seen,
            lower_seen,
            lower_seen,
        ),
    )
    conn.execute(
        """
        INSERT INTO redeem_codes(
            code, status, source_url, source_type, note,
            first_seen_at, last_seen_at, last_status_change_at, last_announced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ABC123XY",
            "expired",
            "https://example.com/upper",
            "monitor",
            "upper",
            upper_seen,
            upper_seen,
            upper_seen,
            None,
        ),
    )
    conn.commit()
    conn.close()

    asyncio.run(storage.initialize())

    conn = sqlite3.connect(storage.database_path)
    rows = conn.execute(
        "SELECT code, status, source_type FROM redeem_codes ORDER BY code"
    ).fetchall()
    conn.close()

    assert rows == [("ABC123XY", "expired", "monitor")]
    assert asyncio.run(storage.get_code_status("abc123xy")) == ("expired", "monitor")


def test_channel_matches_target_supports_thread_parent() -> None:
    assert channel_matches_target(channel_id=123, parent_id=None, target_id=123) is True
    assert channel_matches_target(channel_id=456, parent_id=123, target_id=123) is True
    assert channel_matches_target(channel_id=456, parent_id=999, target_id=123) is False


def test_build_snapshot_candidate_urls_adds_jsdelivr_mirror() -> None:
    assert build_snapshot_candidate_urls(
        "https://raw.githubusercontent.com/MaxFung76/wwm-code-monitor-dc-bot/snapshot-cache/codes_snapshot.json"
    ) == [
        "https://raw.githubusercontent.com/MaxFung76/wwm-code-monitor-dc-bot/snapshot-cache/codes_snapshot.json",
        "https://cdn.jsdelivr.net/gh/MaxFung76/wwm-code-monitor-dc-bot@snapshot-cache/codes_snapshot.json",
    ]


def test_merge_snapshots_prefers_expired_status_from_later_source() -> None:
    observed_at = datetime.now(timezone.utc)
    primary_snapshot = CodeSnapshot(
        source_url="https://example.com/primary",
        observed_at=observed_at,
        codes=[RedeemCode(code="FINALTRUTH", status=CodeStatus.ACTIVE, note="primary")],
    )
    secondary_snapshot = CodeSnapshot(
        source_url="https://example.com/secondary",
        observed_at=observed_at,
        codes=[
            RedeemCode(code="FINALTRUTH", status=CodeStatus.EXPIRED, note="expired"),
            RedeemCode(code="TF37WR876K", status=CodeStatus.ACTIVE, note="active"),
        ],
    )

    merged = merge_snapshots([primary_snapshot, secondary_snapshot])
    status_map = {item.code: item.status for item in merged.codes}

    assert merged.source_url == "https://example.com/primary | https://example.com/secondary"
    assert status_map["FINALTRUTH"] == CodeStatus.EXPIRED
    assert status_map["TF37WR876K"] == CodeStatus.ACTIVE


def test_fetch_primary_monitor_snapshot_falls_back_to_api_when_remote_snapshot_fails() -> None:
    bot = object.__new__(RedeemCodeBot)
    expected_snapshot = CodeSnapshot(
        source_url=DEFAULT_API_URL,
        observed_at=datetime.now(timezone.utc),
        codes=[RedeemCode(code="FALLBACK88", status=CodeStatus.ACTIVE, note=None)],
    )
    bot.settings = SimpleNamespace(
        remote_snapshot_url="https://raw.githubusercontent.com/example/repo/main/snapshot.json",
        request_timeout_seconds=5,
    )

    async def fake_fetch_remote_snapshot(_: str) -> CodeSnapshot:
        request = httpx.Request("GET", "https://example.com/snapshot.json")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)

    async def fake_live_snapshot() -> CodeSnapshot:
        return expected_snapshot

    bot.fetch_remote_snapshot = fake_fetch_remote_snapshot
    bot.monitor = SimpleNamespace(fetch_snapshot=fake_live_snapshot)

    import asyncio

    primary_snapshot, mode = asyncio.run(RedeemCodeBot.fetch_primary_monitor_snapshot(bot))

    assert mode == "codes_api"
    assert primary_snapshot == expected_snapshot

