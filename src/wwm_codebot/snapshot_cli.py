from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from .code_utils import normalize_code
from .models import CodeSnapshot, CodeStatus, RedeemCode
from .snapshot_io import snapshot_to_json
from .yar import DEFAULT_API_URL, YarCodesMonitor


def build_parser() -> argparse.ArgumentParser:
    # GitHub Actions：產 snapshot-cache
    parser = argparse.ArgumentParser(description="Fetch codes and write a snapshot JSON file.")
    parser.add_argument(
        "--api-url",
        default=os.getenv("CODES_API_URL", "").strip(),
        help="Codes API URL (can also be provided via CODES_API_URL env).",
    )
    parser.add_argument("--output", required=True, help="Snapshot JSON output path.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP/browser timeout in seconds.",
    )
    return parser


def merge_code_snapshots(snapshots: list[CodeSnapshot]) -> CodeSnapshot:
    if not snapshots:
        raise ValueError("Cannot merge an empty snapshot list.")
    if len(snapshots) == 1:
        return snapshots[0]

    collected: dict[str, RedeemCode] = {}
    order: list[str] = []
    for snapshot in snapshots:
        for item in snapshot.codes:
            code = normalize_code(item.code)
            existing = collected.get(code)
            candidate = RedeemCode(code=code, status=item.status, note=item.note)
            if existing is None:
                collected[code] = candidate
                order.append(code)
                continue
            if existing.status == CodeStatus.ACTIVE and candidate.status == CodeStatus.EXPIRED:
                collected[code] = candidate

    source_urls = list(dict.fromkeys(snapshot.source_url for snapshot in snapshots))
    return CodeSnapshot(
        source_url=" | ".join(source_urls),
        observed_at=max(snapshot.observed_at for snapshot in snapshots),
        codes=[collected[code] for code in order],
    )


async def run(api_url: str, output: Path, timeout: int) -> None:
    monitor = YarCodesMonitor(api_url=api_url or DEFAULT_API_URL, timeout_seconds=timeout)
    snapshot = await monitor.fetch_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snapshot_to_json(snapshot) + "\n", encoding="utf-8")


def main() -> None:
    args = build_parser().parse_args()
    api_url = args.api_url or os.getenv("CODES_API_URL", "").strip()
    asyncio.run(run(api_url, Path(args.output), args.timeout))


if __name__ == "__main__":
    main()
