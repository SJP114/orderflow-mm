import asyncio
from pathlib import Path

import pytest

from orderflow_mm.collector import MarketDataCollector


class SilentWebSocket:
    async def recv(self) -> str:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_application_idle_timeout_forces_reconnect(tmp_path: Path) -> None:
    collector = MarketDataCollector(
        symbol="BTCUSDT",
        output_root=tmp_path,
        idle_timeout_seconds=0.01,
    )

    with pytest.raises(ConnectionError, match="market-data stream idle"):
        asyncio.run(collector._read_connection(SilentWebSocket()))

    assert collector.stats.idle_timeouts == 1
