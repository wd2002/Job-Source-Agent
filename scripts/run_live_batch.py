"""Run an opt-in live batch and print a demo-friendly JSON report."""

import asyncio
import json
import sys

from app.resolver import JobSourceResolver


async def main(path: str) -> None:
    with open(path, encoding="utf-8") as handle:
        urls = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    results = [await JobSourceResolver().resolve(url) for url in urls]
    successful = sum(result.success for result in results)
    print(json.dumps({
        "total": len(results),
        "successful": successful,
        "success_rate": successful / len(results) if results else 0.0,
        "results": [result.model_dump() for result in results],
    }, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/run_live_batch.py urls.txt")
    asyncio.run(main(sys.argv[1]))
