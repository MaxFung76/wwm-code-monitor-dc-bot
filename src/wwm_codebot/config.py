from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    # Discord token
    discord_token: str
    # 預設面板頻道（可用 /setup_buttons 改）
    discord_channel_id: int
    # 填了 guild 會快一點（sync 指令）
    discord_guild_id: int | None
    # Codes API URL
    codes_api_url: str
    # 遠端 snapshot（避免 VPS 直抓）
    remote_snapshot_url: str | None
    # SQLite path
    database_path: Path
    # 排程頻率（分鐘）
    monitor_interval_minutes: int = 60
    # 抓取 timeout
    request_timeout_seconds: int = 20
    heartbeat_interval_seconds: int = 30
    watchdog_timeout_seconds: int = 180
    healthcheck_max_stale_seconds: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        # 從 .env 讀設定
        load_dotenv()

        # Discord 必填
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("Missing DISCORD_TOKEN in environment.")

        channel_id = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if not channel_id:
            raise ValueError("Missing DISCORD_CHANNEL_ID in environment.")

        # guild 可選
        guild_id_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
        guild_id = int(guild_id_raw) if guild_id_raw else None

        codes_api_url = os.getenv(
            "CODES_API_URL",
            "https://codes-backend.wwmcodes.workers.dev/v1/codes",
        ).strip()
        # snapshot（有設就優先用）
        remote_snapshot_url = os.getenv("REMOTE_SNAPSHOT_URL", "").strip() or None

        # SQLite path
        database_path = Path(
            os.getenv("DATABASE_PATH", "data/redeem_codes.db").strip()
        ).expanduser()

        return cls(
            discord_token=token,
            discord_channel_id=int(channel_id),
            discord_guild_id=guild_id,
            codes_api_url=codes_api_url,
            remote_snapshot_url=remote_snapshot_url,
            database_path=database_path,
            monitor_interval_minutes=int(os.getenv("MONITOR_INTERVAL_MINUTES", "60")),
            request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
            heartbeat_interval_seconds=int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30")),
            watchdog_timeout_seconds=int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "180")),
            healthcheck_max_stale_seconds=int(os.getenv("HEALTHCHECK_MAX_STALE_SECONDS", "120")),
        )
