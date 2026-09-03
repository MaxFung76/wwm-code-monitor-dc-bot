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
    # Bahamut URL
    forum_url: str
    # Arlen URL（空字串=關）
    arlen_codes_url: str | None
    # 遠端 snapshot（避免 VPS 直抓）
    remote_snapshot_url: str | None
    # SQLite path
    database_path: Path
    # Arlen expired 連續確認次數（complete 同步才累積）
    arlen_expired_confirmations: int = 5
    # 排程頻率（分鐘）
    monitor_interval_minutes: int = 60
    # 抓取 timeout
    request_timeout_seconds: int = 20

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

        # Bahamut URL
        forum_url = os.getenv(
            "FORUM_URL",
            "https://forum.gamer.com.tw/C.php?bsn=75703&snA=388",
        ).strip()
        # Arlen URL（空字串=關）
        arlen_codes_url = (
            os.getenv(
                "ARLEN_CODES_URL",
                "https://www.arlenfuture.com/games/where-winds-meet/codes/",
            ).strip()
            or None
        )
        # snapshot（有設就優先用）
        remote_snapshot_url = os.getenv("REMOTE_SNAPSHOT_URL", "").strip() or None
        arlen_expired_confirmations = int(os.getenv("ARLEN_EXPIRED_CONFIRMATIONS", "5"))

        # SQLite path
        database_path = Path(
            os.getenv("DATABASE_PATH", "data/redeem_codes.db").strip()
        ).expanduser()

        return cls(
            discord_token=token,
            discord_channel_id=int(channel_id),
            discord_guild_id=guild_id,
            forum_url=forum_url,
            arlen_codes_url=arlen_codes_url,
            remote_snapshot_url=remote_snapshot_url,
            arlen_expired_confirmations=arlen_expired_confirmations,
            database_path=database_path,
            monitor_interval_minutes=int(os.getenv("MONITOR_INTERVAL_MINUTES", "60")),
            request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
        )
