from __future__ import annotations

import asyncio

from .config import Settings
from .discord_bot import RedeemCodeBot
from .storage import Storage


async def main() -> None:
    # 讀取環境設定（.env）並建立全域依賴
    settings = Settings.from_env()
    # SQLite：負責兌換碼狀態、歷史觀測、面板狀態與已讀紀錄
    storage = Storage(settings.database_path)
    # Discord Bot：整合監控來源、寫入 Storage、並在指定頻道維持置底面板
    bot = RedeemCodeBot(settings=settings, storage=storage)
    await bot.start(settings.discord_token)


if __name__ == "__main__":
    # 入口點：以 asyncio 啟動 bot event loop
    asyncio.run(main())
