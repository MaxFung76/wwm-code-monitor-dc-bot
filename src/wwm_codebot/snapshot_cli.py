from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .bahamut import BahamutMonitor
from .snapshot_io import snapshot_to_json


def build_parser() -> argparse.ArgumentParser:
    # 用於 GitHub Actions 產生 snapshot-cache（避免 VPS 直連巴哈被限流）
    parser = argparse.ArgumentParser(description="Fetch Bahamut codes and write a snapshot JSON file.")
    parser.add_argument("--url", required=True, help="Bahamut article URL.")
    parser.add_argument("--output", required=True, help="Snapshot JSON output path.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP/browser timeout in seconds.",
    )
    return parser


async def run(url: str, output: Path, timeout: int) -> None:
    # 以 BahamutMonitor 抓取並輸出成 snapshot JSON
    monitor = BahamutMonitor(forum_url=url, timeout_seconds=timeout)
    snapshot = await monitor.fetch_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snapshot_to_json(snapshot) + "\n", encoding="utf-8")


def main() -> None:
    # CLI 入口：同步執行一次抓取
    args = build_parser().parse_args()
    asyncio.run(run(args.url, Path(args.output), args.timeout))


if __name__ == "__main__":
    main()
