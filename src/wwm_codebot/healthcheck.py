from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone

from .config import Settings


def main() -> int:
    settings = Settings.from_env()
    conn = sqlite3.connect(settings.database_path)
    try:
        row = conn.execute(
            "SELECT value FROM bot_state WHERE key = ?",
            ("bot_heartbeat_at",),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return 1

    raw = str(row[0])
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return 1

    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age = (now - last).total_seconds()
    if age > int(settings.healthcheck_max_stale_seconds):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

