from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets

from orderflow_mm.schema import event_id, parse_message
from orderflow_mm.storage import PartitionedParquetWriter

LOGGER = logging.getLogger(__name__)


@dataclass
class CollectorStats:
    started_at: str
    finished_at: str | None = None
    received: dict[str, int] = field(default_factory=lambda: {"book_ticker": 0, "trade": 0})
    accepted: dict[str, int] = field(default_factory=lambda: {"book_ticker": 0, "trade": 0})
    duplicates: dict[str, int] = field(default_factory=lambda: {"book_ticker": 0, "trade": 0})
    parse_errors: int = 0
    reconnects: int = 0
    idle_timeouts: int = 0
    max_queue_size: int = 0
    files_written: int = 0
    rows_written: int = 0


class MarketDataCollector:
    def __init__(
        self,
        symbol: str,
        output_root: Path,
        batch_size: int = 50_000,
        flush_seconds: float = 300.0,
        queue_size: int = 100_000,
        idle_timeout_seconds: float = 10.0,
    ) -> None:
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        self.symbol = symbol.lower()
        self.output_root = output_root
        self.flush_seconds = flush_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=queue_size)
        self.writer = PartitionedParquetWriter(output_root, batch_size=batch_size)
        self.stop_event = asyncio.Event()
        self.last_event_ids: dict[str, int] = {}
        self.stats = CollectorStats(started_at=_utc_now())

    @property
    def websocket_url(self) -> str:
        streams = f"{self.symbol}@bookTicker/{self.symbol}@trade"
        return f"wss://data-stream.binance.vision/stream?streams={streams}"

    async def run(self, duration_seconds: float | None = None) -> CollectorStats:
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_name, self.stop_event.set)
            except NotImplementedError:
                pass

        if duration_seconds is not None:
            loop.call_later(duration_seconds, self.stop_event.set)

        consumer_task = asyncio.create_task(self._consume(), name="parquet-consumer")
        try:
            await self._produce()
        finally:
            self.stop_event.set()
            await self.queue.join()
            await consumer_task
            self.writer.flush()
            self.stats.finished_at = _utc_now()
            self.stats.files_written = self.writer.files_written
            self.stats.rows_written = self.writer.rows_written
            self._write_manifest()
        return self.stats

    async def _produce(self) -> None:
        backoff_seconds = 1.0
        connected_once = False
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(
                    self.websocket_url,
                    open_timeout=15,
                    close_timeout=5,
                    max_queue=4096,
                    proxy=None,
                ) as websocket:
                    if connected_once:
                        self.stats.reconnects += 1
                    connected_once = True
                    backoff_seconds = 1.0
                    LOGGER.info("connected to %s", self.websocket_url)
                    await self._read_connection(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                LOGGER.warning("stream error (%s); reconnecting in %.1fs", exc, backoff_seconds)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=backoff_seconds)
                except TimeoutError:
                    pass
                backoff_seconds = min(backoff_seconds * 2, 30.0)

    async def _read_connection(self, websocket: Any) -> None:
        last_message_at = time.monotonic()
        flow_confirmed = False
        while not self.stop_event.is_set():
            try:
                raw = await asyncio.wait_for(
                    websocket.recv(), timeout=min(1.0, self.idle_timeout_seconds)
                )
            except TimeoutError:
                idle_seconds = time.monotonic() - last_message_at
                if idle_seconds >= self.idle_timeout_seconds:
                    self.stats.idle_timeouts += 1
                    raise ConnectionError(
                        f"market-data stream idle for {idle_seconds:.1f} seconds"
                    ) from None
                continue
            last_message_at = time.monotonic()
            receive_ts_ns = time.time_ns()
            try:
                stream_name, row = parse_message(json.loads(raw), receive_ts_ns)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                self.stats.parse_errors += 1
                LOGGER.exception("unable to parse market-data message")
                continue

            self.stats.received[stream_name] += 1
            identifier = event_id(stream_name, row)
            previous = self.last_event_ids.get(stream_name)
            if previous is not None and identifier <= previous:
                self.stats.duplicates[stream_name] += 1
                continue
            self.last_event_ids[stream_name] = identifier

            await self.queue.put((stream_name, row))
            self.stats.accepted[stream_name] += 1
            if not flow_confirmed:
                LOGGER.info(
                    "market-data flow active (stream=%s event_id=%s)",
                    stream_name,
                    identifier,
                )
                flow_confirmed = True
            self.stats.max_queue_size = max(self.stats.max_queue_size, self.queue.qsize())

    async def _consume(self) -> None:
        last_flush = time.monotonic()
        while not self.stop_event.is_set() or not self.queue.empty():
            timeout = max(0.05, self.flush_seconds - (time.monotonic() - last_flush))
            try:
                stream_name, row = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            except TimeoutError:
                self.writer.flush()
                last_flush = time.monotonic()
                continue

            try:
                self.writer.add(stream_name, row)
            finally:
                self.queue.task_done()

            if time.monotonic() - last_flush >= self.flush_seconds:
                self.writer.flush()
                last_flush = time.monotonic()

        self.writer.flush()

    def _write_manifest(self) -> None:
        manifest_dir = self.output_root.parent / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / f"run-{time.time_ns()}.json"
        path.write_text(json.dumps(asdict(self.stats), indent=2), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
