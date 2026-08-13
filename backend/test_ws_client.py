#!/usr/bin/env python3
"""Standalone WebSocket client for manual live-feed testing."""

import asyncio
import json
import sys

import websockets

URL = "ws://localhost:8000/ws/live-feed"
DURATION_SECONDS = 60


async def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else DURATION_SECONDS
    print(f"Connecting to {URL} for {duration}s...")

    async with websockets.connect(URL) as websocket:
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
