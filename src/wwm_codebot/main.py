from __future__ import annotations

import asyncio

from .config import Settings
from .discord_bot import RedeemCodeBot
from .storage import Storage


async def main() -> None:
    settings = Settings.from_env()
    storage = Storage(settings.database_path)
    bot = RedeemCodeBot(settings=settings, storage=storage)
    await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
