from __future__ import annotations

from pathlib import Path

from wwm_codebot.bahamut import parse_bahamut_codes
from wwm_codebot.models import CodeStatus
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
