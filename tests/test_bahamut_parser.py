from __future__ import annotations

from pathlib import Path

import pytest

from wwm_codebot.bahamut import _ensure_article_html, parse_bahamut_codes
from wwm_codebot.models import CodeSnapshot, CodeStatus, RedeemCode
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
    assert status_map["hh6am6c8rf"] == CodeStatus.ACTIVE
    assert status_map["YYP4QNC7NQ"] == CodeStatus.ACTIVE
    assert status_map["AC46AQH368"] == CodeStatus.EXPIRED
    assert status_map["GOOSENEWS"] == CodeStatus.EXPIRED
    assert status_map["DEVLOG2601"] == CodeStatus.EXPIRED
    assert "1182577423678713917" not in status_map


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
    assert [row.code for row in reactivated_rows] == ["UNSEEN123"]
