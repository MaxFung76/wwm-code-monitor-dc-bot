from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from wwm_codebot.arlen import parse_arlen_codes
from wwm_codebot.bahamut import _ensure_article_html, parse_bahamut_codes
from wwm_codebot.discord_bot import (
    build_snapshot_candidate_urls,
    channel_matches_target,
    merge_snapshots,
    RedeemCodeBot,
)
from wwm_codebot.models import CodeSnapshot, CodeStatus, RedeemCode
from wwm_codebot.snapshot_cli import merge_code_snapshots
from wwm_codebot.snapshot_io import snapshot_from_json, snapshot_to_json
from wwm_codebot.storage import Storage


def test_parse_bahamut_codes_marks_active_and_expired() -> None:
    html = """
    <div class="c-article__content">
      <div>WWMDEVTALK</div>
      <div>TF37WR876K</div>
      <div><font color="unset">GOHOME123</font></div>
      <div>hh6am6c8rf<br>YYP4QNC7NQ</div>
      <div>AC46AQH368</div>
      <div><strike>AC46AQH368</strike></div>
      <div>1182577423678713917</div>
      <div><strike>GOOSENEWS （3/31到期）</strike></div>
      <div><del>DEVLOG2601</del></div>
    </div>
    """

    snapshot = parse_bahamut_codes(html, "https://example.com")
    status_map = {item.code: item.status for item in snapshot.codes}

    assert status_map["WWMDEVTALK"] == CodeStatus.ACTIVE
    assert status_map["TF37WR876K"] == CodeStatus.ACTIVE
    assert status_map["GOHOME123"] == CodeStatus.ACTIVE
    assert status_map["HH6AM6C8RF"] == CodeStatus.ACTIVE
    assert status_map["YYP4QNC7NQ"] == CodeStatus.ACTIVE
    assert status_map["AC46AQH368"] == CodeStatus.EXPIRED
    assert status_map["GOOSENEWS"] == CodeStatus.EXPIRED
    assert status_map["DEVLOG2601"] == CodeStatus.EXPIRED
    assert "1182577423678713917" not in status_map


def test_parse_arlen_codes_marks_active_and_expired() -> None:
    html = """
    <html>
      <body>
        <section>
          <h2>✅ 有效兌換碼103 個</h2>
          <div>長鳴玉 ×3</div>
          <div>PSEEQPHJ83</div>
          <div>FINALTRUTH</div>
        </section>
        <section>
          <h2>❌ 失效兌換碼269 個</h2>
          <div>KPCA7MF6KN 失效 複製 ※</div>
          <div>WWMR3DD1T 失效 複製 ※</div>
          <div>PSEEQPHJ83 失效 複製 ※</div>
        </section>
      </body>
    </html>
    """

    snapshot = parse_arlen_codes(html, "https://example.com/arlen")
    status_map = {item.code: item.status for item in snapshot.codes}

    assert status_map["PSEEQPHJ83"] == CodeStatus.EXPIRED
    assert status_map["FINALTRUTH"] == CodeStatus.ACTIVE
    assert status_map["KPCA7MF6KN"] == CodeStatus.EXPIRED
    assert status_map["WWMR3DD1T"] == CodeStatus.EXPIRED


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
                item
                for item in parse_bahamut_codes(
                    """
                    <div class="c-article__content">
                      <div>NEWCODE88</div>
                      <div><strike>OLDCODE99</strike></div>
                    </div>
                    """,
                    "https://example.com",
                ).codes
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
                item
                for item in parse_bahamut_codes(
                    """
                    <div class="c-article__content">
                      <div>NEWCODE88</div>
                      <div><strike>OLDCODE99</strike></div>
                    </div>
                    """,
                    "https://example.com",
                ).codes
            ],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    assert third.new_active_codes == []
    assert third.first_seen_active_codes == []


def test_ensure_article_html_rejects_maintenance_page() -> None:
    html = """
    <html>
      <head><title>巴哈姆特電玩資訊站 - 系統維修中</title></head>
      <body>系統維修中，請稍後再試。</body>
    </html>
    """

    with pytest.raises(RuntimeError, match="maintenance"):
        _ensure_article_html(html, source="test")


def test_snapshot_json_round_trip() -> None:
    snapshot = CodeSnapshot(
        source_url="https://example.com",
        observed_at=parse_bahamut_codes(
            '<div class="c-article__content"><div>TESTCODE1</div></div>',
            "https://example.com",
        ).observed_at,
        codes=[
            RedeemCode(code="TESTCODE1", status=CodeStatus.ACTIVE, note="note"),
            RedeemCode(code="TESTCODE2", status=CodeStatus.EXPIRED, note=None),
        ],
    )

    restored = snapshot_from_json(snapshot_to_json(snapshot))

    assert restored.source_url == snapshot.source_url
    assert restored.observed_at == snapshot.observed_at
    assert [(item.code, item.status, item.note) for item in restored.codes] == [
        ("TESTCODE1", CodeStatus.ACTIVE, "note"),
        ("TESTCODE2", CodeStatus.EXPIRED, None),
    ]


def test_storage_initialize_removes_numeric_only_codes(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio
    import sqlite3
    from datetime import datetime, timezone

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
    from datetime import datetime, timedelta, timezone

    asyncio.run(storage.initialize())

    start = datetime.now(timezone.utc)
    lower_seen = start.isoformat()
    upper_seen = (start + timedelta(minutes=5)).isoformat()

    conn = sqlite3.connect(storage.database_path)
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
        "https://raw.githubusercontent.com/MaxFung76/wwm-code-monitor-dc-bot/snapshot-cache/bahamut_snapshot.json"
    ) == [
        "https://raw.githubusercontent.com/MaxFung76/wwm-code-monitor-dc-bot/snapshot-cache/bahamut_snapshot.json",
        "https://cdn.jsdelivr.net/gh/MaxFung76/wwm-code-monitor-dc-bot@snapshot-cache/bahamut_snapshot.json",
    ]


def test_merge_snapshots_prefers_expired_status_from_later_source() -> None:
    bahamut_snapshot = CodeSnapshot(
        source_url="https://example.com/bahamut",
        observed_at=parse_bahamut_codes(
            '<div class="c-article__content"><div>FINALTRUTH</div></div>',
            "https://example.com/bahamut",
        ).observed_at,
        codes=[RedeemCode(code="FINALTRUTH", status=CodeStatus.ACTIVE, note="bahamut")],
    )
    arlen_snapshot = CodeSnapshot(
        source_url="https://example.com/arlen",
        observed_at=bahamut_snapshot.observed_at,
        codes=[
            RedeemCode(code="FINALTRUTH", status=CodeStatus.EXPIRED, note="arlen expired"),
            RedeemCode(code="TF37WR876K", status=CodeStatus.ACTIVE, note="arlen active"),
        ],
    )

    merged = merge_snapshots([bahamut_snapshot, arlen_snapshot])
    status_map = {item.code: item.status for item in merged.codes}

    assert merged.source_url == "https://example.com/bahamut | https://example.com/arlen"
    assert status_map["FINALTRUTH"] == CodeStatus.EXPIRED
    assert status_map["TF37WR876K"] == CodeStatus.ACTIVE


def test_snapshot_cli_merge_code_snapshots_merges_sources() -> None:
    bahamut_snapshot = CodeSnapshot(
        source_url="https://example.com/bahamut",
        observed_at=parse_bahamut_codes(
            '<div class="c-article__content"><div>FINALTRUTH</div></div>',
            "https://example.com/bahamut",
        ).observed_at,
        codes=[RedeemCode(code="FINALTRUTH", status=CodeStatus.ACTIVE, note="bahamut")],
    )
    arlen_snapshot = CodeSnapshot(
        source_url="https://example.com/arlen",
        observed_at=bahamut_snapshot.observed_at,
        codes=[RedeemCode(code="FINALTRUTH", status=CodeStatus.EXPIRED, note="arlen expired")],
    )

    merged = merge_code_snapshots([bahamut_snapshot, arlen_snapshot])
    status_map = {item.code: item.status for item in merged.codes}

    assert merged.source_url == "https://example.com/bahamut | https://example.com/arlen"
    assert status_map["FINALTRUTH"] == CodeStatus.EXPIRED


def test_fetch_monitor_snapshot_falls_back_to_live_when_remote_snapshot_fails() -> None:
    bot = object.__new__(RedeemCodeBot)
    expected_snapshot = CodeSnapshot(
        source_url="https://example.com/live",
        observed_at=parse_bahamut_codes(
            '<div class="c-article__content"><div>FALLBACK88</div></div>',
            "https://example.com/live",
        ).observed_at,
        codes=[RedeemCode(code="FALLBACK88", status=CodeStatus.ACTIVE, note="live")],
    )
    bot.settings = SimpleNamespace(
        remote_snapshot_url="https://raw.githubusercontent.com/example/repo/main/snapshot.json"
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

    snapshot, mode, complete = asyncio.run(RedeemCodeBot.fetch_monitor_snapshot(bot))

    assert mode == "live_bahamut"
    assert complete is True
    assert snapshot == expected_snapshot


def test_fetch_monitor_snapshot_merges_arlen_source() -> None:
    bot = object.__new__(RedeemCodeBot)
    live_snapshot = CodeSnapshot(
        source_url="https://example.com/live",
        observed_at=parse_bahamut_codes(
            '<div class="c-article__content"><div>FINALTRUTH</div></div>',
            "https://example.com/live",
        ).observed_at,
        codes=[RedeemCode(code="FINALTRUTH", status=CodeStatus.ACTIVE, note="live")],
    )
    arlen_snapshot = CodeSnapshot(
        source_url="https://example.com/arlen",
        observed_at=live_snapshot.observed_at,
        codes=[
            RedeemCode(code="FINALTRUTH", status=CodeStatus.EXPIRED, note="arlen"),
            RedeemCode(code="TF37WR876K", status=CodeStatus.ACTIVE, note="arlen"),
        ],
    )
    bot.settings = SimpleNamespace(remote_snapshot_url=None)
    bot.monitor = SimpleNamespace(fetch_snapshot=lambda: None)

    async def fake_primary() -> tuple[CodeSnapshot, str]:
        return live_snapshot, "live_bahamut"

    async def fake_arlen() -> CodeSnapshot:
        return arlen_snapshot

    bot.fetch_primary_monitor_snapshot = fake_primary
    bot.arlen_monitor = SimpleNamespace(fetch_snapshot=fake_arlen)

    import asyncio

    snapshot, mode, complete = asyncio.run(RedeemCodeBot.fetch_monitor_snapshot(bot))

    status_map = {item.code: item.status for item in snapshot.codes}
    assert mode == "live_bahamut+arlen_codes"
    assert complete is True
    assert status_map["FINALTRUTH"] == CodeStatus.EXPIRED
    assert status_map["TF37WR876K"] == CodeStatus.ACTIVE


def test_fetch_monitor_snapshot_is_partial_when_arlen_fails() -> None:
    bot = object.__new__(RedeemCodeBot)
    live_snapshot = CodeSnapshot(
        source_url="https://example.com/live",
        observed_at=parse_bahamut_codes(
            '<div class="c-article__content"><div>FINALTRUTH</div></div>',
            "https://example.com/live",
        ).observed_at,
        codes=[RedeemCode(code="FINALTRUTH", status=CodeStatus.ACTIVE, note="live")],
    )
    bot.settings = SimpleNamespace(remote_snapshot_url=None)

    async def fake_primary() -> tuple[CodeSnapshot, str]:
        return live_snapshot, "live_bahamut"

    async def fake_arlen_fail() -> CodeSnapshot:
        raise RuntimeError("arlen down")

    bot.fetch_primary_monitor_snapshot = fake_primary
    bot.arlen_monitor = SimpleNamespace(fetch_snapshot=fake_arlen_fail)

    import asyncio

    snapshot, mode, complete = asyncio.run(RedeemCodeBot.fetch_monitor_snapshot(bot))

    assert snapshot.source_url == "https://example.com/live"
    assert mode == "live_bahamut"
    assert complete is False


def test_storage_first_seen_active_codes_only_counts_initial_inserts(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "codes.db")

    import asyncio

    asyncio.run(storage.initialize())

    first = asyncio.run(
        storage.reconcile_codes(
            [RedeemCode(code="FLIPCODE1", status=CodeStatus.EXPIRED, note="expired first")],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    assert first.first_seen_active_codes == []

    second = asyncio.run(
        storage.reconcile_codes(
            [RedeemCode(code="FLIPCODE1", status=CodeStatus.ACTIVE, note="active later")],
            source_url="https://example.com",
            source_type="monitor",
        )
    )
    assert second.new_active_codes == []
    assert second.first_seen_active_codes == []

    status = asyncio.run(storage.get_code_status("FLIPCODE1"))
    assert status is not None
    assert status[0] == CodeStatus.EXPIRED.value
