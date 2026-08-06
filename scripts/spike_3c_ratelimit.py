"""Spike 3c: find the Onshape rate-limit ceiling.

Step 4 will build exponential backoff. Designing a retry budget without knowing
what we are backing off from is guesswork, and the FeatureScript loop (up to 3
retries per attempt, across several students) is exactly what would exhaust it.

Deliberately polite: it first checks whether Onshape advertises limits in
response headers, and only falls back to a bounded burst if it does not. The
burst stops at the first 429 and is capped, so this is not a stress test.

    python scripts/spike_3c_ratelimit.py [max_requests]
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402

MAX_REQUESTS = int(sys.argv[1]) if len(sys.argv) > 1 else 150
CONCURRENCY = 8


async def main() -> int:
    async with OnshapeApiClient.from_env() as c:
        # 1. Does Onshape just tell us the limit?
        r = await c.get("/documents", query={"limit": 1})
        interesting = {
            k: v for k, v in r.headers.items()
            if any(t in k.lower() for t in ("rate", "limit", "quota", "retry", "remaining"))
        }
        print("--- rate-limit headers on a normal 200 ---")
        if interesting:
            for k, v in sorted(interesting.items()):
                print(f"  {k}: {v}")
        else:
            print("  none advertised -- Onshape does not publish limits in headers")

        # 2. Bounded burst until the first 429.
        print(f"\n--- bounded burst: up to {MAX_REQUESTS} requests, "
              f"concurrency {CONCURRENCY}, stopping at the first 429 ---")

        sent = 0
        first_429 = None
        retry_after = None
        stop = asyncio.Event()
        started = time.monotonic()
        lock = asyncio.Lock()

        async def worker():
            nonlocal sent, first_429, retry_after
            while not stop.is_set():
                async with lock:
                    if sent >= MAX_REQUESTS:
                        stop.set()
                        return
                    sent += 1
                    n = sent
                try:
                    await c.get("/documents", query={"limit": 1})
                except Exception as exc:  # noqa: BLE001
                    resp = getattr(exc, "response", None)
                    code = getattr(resp, "status_code", None)
                    if code == 429:
                        first_429 = n
                        retry_after = resp.headers.get("Retry-After")
                        stop.set()
                        return
                    print(f"  request {n}: unexpected {code or type(exc).__name__}")

        await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
        elapsed = time.monotonic() - started

        print(f"  sent      : {sent} requests in {elapsed:.1f}s "
              f"({sent / elapsed:.1f} req/s)")
        if first_429:
            print(f"  FIRST 429 : after {first_429} requests")
            print(f"  Retry-After header: {retry_after or '(not sent)'}")
        else:
            print(f"  no 429 within {sent} requests -- the ceiling is above this rate")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
