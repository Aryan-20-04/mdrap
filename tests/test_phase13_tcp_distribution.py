"""
Phase 13: TCP Distribution Concurrency Tests (CONC-02).
Validates:
- Concurrent broadcast across multiple clients via asyncio.gather.
- Slow client does not block or accumulate sequential timeout delays for fast clients.
- Clean eviction and telemetry tracking for dead/stalling clients.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from mdrap.gateway_tcp import TCPGatewayServer


@pytest.mark.asyncio
async def test_conc_02_concurrent_broadcast_without_head_of_line_blocking():
    gw = TCPGatewayServer(host="127.0.0.1", port=0, require_auth=False)

    fast_writer1 = MagicMock(spec=asyncio.StreamWriter)
    fast_writer1.drain = AsyncMock(return_value=None)

    fast_writer2 = MagicMock(spec=asyncio.StreamWriter)
    fast_writer2.drain = AsyncMock(return_value=None)

    # Slow writer that times out
    slow_writer = MagicMock(spec=asyncio.StreamWriter)

    async def slow_drain():
        await asyncio.sleep(0.5)

    slow_writer.drain = AsyncMock(side_effect=slow_drain)

    gw.clients.add(fast_writer1)
    gw.clients.add(slow_writer)
    gw.clients.add(fast_writer2)
    gw._stats["connected"] = 3

    t0 = asyncio.get_event_loop().time()
    await gw.broadcast({"msg": "market_tick", "price": 100.0})
    t1 = asyncio.get_event_loop().time()

    elapsed = t1 - t0
    # The timeout in _send_to_client is 0.05s. With concurrent gather, the broadcast should complete
    # in ~0.05-0.10s, NOT sequential cumulative blocking (0.5s or sum of timeouts).
    assert elapsed < 0.20, (
        f"Broadcast took too long ({elapsed:.3f}s), indicating head-of-line blocking!"
    )

    # Both fast writers received data
    assert fast_writer1.write.called
    assert fast_writer2.write.called
    assert slow_writer.write.called

    # Stats: 2 sent, 1 dropped (slow client evicted)
    assert gw._stats["sent"] == 2
    assert gw._stats["dropped"] == 1
    assert slow_writer not in gw.clients
    assert fast_writer1 in gw.clients
    assert fast_writer2 in gw.clients
