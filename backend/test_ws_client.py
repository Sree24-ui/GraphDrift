#!/usr/bin/env python3
"""Standalone WebSocket client for manual live-feed testing."""

import asyncio
import json
import os
import sys

import websockets

DEFAULT_DURATION_SECONDS = 60


def live_feed_url() -> str:
    """Return the configured live-feed endpoint for manual testing."""
    url = os.getenv("LIVE_FEED_WS_URL", "").strip()
    if not url:
        raise RuntimeError("LIVE_FEED_WS_URL must be set")
    return url


async def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DURATION_SECONDS
    url = live_feed_url()
    print(f"Connecting to {url} for {duration}s...")

    async with websockets.connect(url) as websocket:
        print("Connected. Waiting for messages...\n")

        async def _reader() -> None:
            async for raw in websocket:
                message = json.loads(raw)
                print(json.dumps(message, indent=2))
                print("---")

        reader = asyncio.create_task(_reader())
        try:
            await asyncio.sleep(duration)
        finally:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass

    print("\nDisconnected cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
