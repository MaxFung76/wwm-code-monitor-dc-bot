from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    discord_token: str
    discord_channel_id: int
    discord_guild_id: int | None
    forum_url: str
    database_path: Path
    monitor_interval_minutes: int = 10
    request_timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("Missing DISCORD_TOKEN in environment.")

        channel_id = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if not channel_id:
            raise ValueError("Missing DISCORD_CHANNEL_ID in environment.")

        guild_id_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
        guild_id = int(guild_id_raw) if guild_id_raw else None

        forum_url = os.getenv(
            "FORUM_URL",
            "https://forum.gamer.com.tw/C.php?bsn=75703&snA=388",
        ).strip()

        database_path = Path(
            os.getenv("DATABASE_PATH", "data/redeem_codes.db").strip()
        ).expanduser()

        return cls(
            discord_token=token,
            discord_channel_id=int(channel_id),
            discord_guild_id=guild_id,
            forum_url=forum_url,
            database_path=database_path,
            monitor_interval_minutes=int(os.getenv("MONITOR_INTERVAL_MINUTES", "10")),
            request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
        )
