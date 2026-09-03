from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    # Discord Bot Token
    discord_token: str
    # 面板預設頻道（之後 /setup_buttons 可覆蓋）
    discord_channel_id: int
    # 指定 guild 時，slash command 同步會更快
    discord_guild_id: int | None
    # 巴哈來源（live fetch 或供 snapshot workflow 使用）
    forum_url: str
    # 額外來源：阿冷整理頁（可設空字串停用）
    arlen_codes_url: str | None
    # 遠端 snapshot（有設時優先用 snapshot，減少 VPS 直抓巴哈造成的 403/429 風險）
    remote_snapshot_url: str | None
    # SQLite 檔案路徑
    database_path: Path
    # Arlen 的失效碼需要連續確認次數（完整同步時才計數）
    arlen_expired_confirmations: int = 5
    # 監控排程頻率（分鐘）
    monitor_interval_minutes: int = 60
    # HTTP / Playwright 抓取 timeout
    request_timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "Settings":
        # 支援從 .env 載入，便於 Docker / VPS 部署
        load_dotenv()

        # Discord 必填設定
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("Missing DISCORD_TOKEN in environment.")

        channel_id = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if not channel_id:
            raise ValueError("Missing DISCORD_CHANNEL_ID in environment.")

        # guild 為可選：填了可加速 slash command 上線
        guild_id_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
        guild_id = int(guild_id_raw) if guild_id_raw else None

        # 主要監控來源（巴哈文章）
        forum_url = os.getenv(
            "FORUM_URL",
            "https://forum.gamer.com.tw/C.php?bsn=75703&snA=388",
        ).strip()
        # 額外來源（可透過設空字串停用）
        arlen_codes_url = (
            os.getenv(
                "ARLEN_CODES_URL",
                "https://www.arlenfuture.com/games/where-winds-meet/codes/",
            ).strip()
            or None
        )
        # 遠端 snapshot（有設會優先使用）
        remote_snapshot_url = os.getenv("REMOTE_SNAPSHOT_URL", "").strip() or None
        arlen_expired_confirmations = int(os.getenv("ARLEN_EXPIRED_CONFIRMATIONS", "5"))

        # SQLite 路徑：容器內通常固定掛載在 /app/data
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
