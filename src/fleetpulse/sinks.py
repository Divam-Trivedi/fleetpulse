from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path

from .config import SinkSettings

log = logging.getLogger(__name__)


class Sink(ABC):
    """Where telemetry goes. A FirehoseSink will drop in here later."""

    @abstractmethod
    async def write(self, record: dict) -> None: ...

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        await self.flush()


class NullSink(Sink):
    async def write(self, record: dict) -> None:
        pass


class StdoutSink(Sink):
    async def write(self, record: dict) -> None:
        sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")


class JsonlSink(Sink):
    def __init__(self, path: str, flush_every: int = 200):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(p, "a", buffering=1 << 16)
        self._n = 0
        self._flush_every = flush_every

    async def write(self, record: dict) -> None:
        self._f.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._n += 1
        if self._n % self._flush_every == 0:
            self._f.flush()

    async def flush(self) -> None:
        self._f.flush()

    async def close(self) -> None:
        self._f.close()


class FirehoseSink(Sink):
    """Non-blocking batched writer for Kinesis Data Firehose.

    write() only appends to an in-memory buffer, so robot timing never depends on
    network latency. A background task ships batches (500 records / 4 MiB limits)
    with several PutRecordBatch calls in flight. Failed records are retried with
    exponential backoff. If the buffer exceeds max_buffer, new records are dropped
    and counted instead of stalling the simulation.
    """

    MAX_RECORDS = 500
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, stream_name: str, region: str | None = None, profile: str | None = None,
                 batch_size: int = 500, flush_interval_s: float = 1.0, max_retries: int = 5,
                 max_buffer: int = 100_000, concurrency: int = 4, client=None):
        if client is None:
            import boto3  # lazy: only needed for real AWS runs

            session = boto3.Session(profile_name=profile, region_name=region)
            client = session.client("firehose")
        self._client = client
        self._stream = stream_name
        self._batch_size = min(batch_size, self.MAX_RECORDS)
        self._interval = flush_interval_s
        self._max_retries = max_retries
        self._max_buffer = max_buffer
        self._concurrency = concurrency
        self._buf: deque[bytes] = deque()
        self._wake = asyncio.Event()
        self._closing = False
        self._task: asyncio.Task | None = None
        self._sem: asyncio.Semaphore | None = None
        self.stats = {"sent": 0, "dropped": 0, "retries": 0, "calls": 0}

    async def write(self, record: dict) -> None:
        if self._task is None:
            self._sem = asyncio.Semaphore(self._concurrency)
            self._task = asyncio.create_task(self._flusher())
        if len(self._buf) >= self._max_buffer:
            self.stats["dropped"] += 1
            return
        # Firehose concatenates records with no delimiter, so each one ends in a newline.
        self._buf.append((json.dumps(record, separators=(",", ":")) + "\n").encode())
        if len(self._buf) >= self._batch_size:
            self._wake.set()

    async def _flusher(self) -> None:
        while not self._closing:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self._drain()

    async def _drain(self) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._concurrency)
        jobs = [asyncio.create_task(self._ship(c)) for c in self._take_chunks()]
        if jobs:
            await asyncio.gather(*jobs)

    def _take_chunks(self):
        chunk, size = [], 0
        while self._buf:
            item = self._buf.popleft()
            if chunk and (len(chunk) >= self.MAX_RECORDS or size + len(item) > self.MAX_BYTES):
                yield chunk
                chunk, size = [], 0
            chunk.append(item)
            size += len(item)
        if chunk:
            yield chunk

    async def _ship(self, chunk: list[bytes]) -> None:
        async with self._sem:
            sent, dropped, retries, calls = await asyncio.to_thread(self._send, chunk)
        self.stats["sent"] += sent
        self.stats["dropped"] += dropped
        self.stats["retries"] += retries
        self.stats["calls"] += calls

    def _send(self, chunk: list[bytes]) -> tuple[int, int, int, int]:
        """Runs in a worker thread. Returns (sent, dropped, retries, calls)."""
        from botocore.exceptions import BotoCoreError, ClientError

        pending, sent, retries, calls = chunk, 0, 0, 0
        for attempt in range(self._max_retries + 1):
            try:
                calls += 1
                resp = self._client.put_record_batch(
                    DeliveryStreamName=self._stream,
                    Records=[{"Data": d} for d in pending],
                )
                failed = [d for d, r in zip(pending, resp["RequestResponses"]) if "ErrorCode" in r]
                sent += len(pending) - len(failed)
                if not failed:
                    return sent, 0, retries, calls
                pending = failed
            except (ClientError, BotoCoreError) as exc:
                log.warning("Firehose call failed (attempt %d): %s", attempt + 1, exc)
            retries += 1
            time.sleep(min(0.2 * 2**attempt, 5.0))
        log.error("Dropped %d records after %d retries", len(pending), self._max_retries)
        return sent, len(pending), retries, calls

    async def flush(self) -> None:
        await self._drain()

    async def close(self) -> None:
        self._closing = True
        self._wake.set()
        if self._task:
            await self._task
        await self._drain()


def create_sink(cfg: SinkSettings) -> Sink:
    if cfg.type == "stdout":
        return StdoutSink()
    if cfg.type == "jsonl":
        return JsonlSink(cfg.path)
    if cfg.type == "null":
        return NullSink()
    if cfg.type == "firehose":
        if not cfg.stream_name:
            raise ValueError("sink.stream_name is required for the firehose sink")
        return FirehoseSink(cfg.stream_name, cfg.region, cfg.profile, cfg.batch_size, cfg.flush_interval_s)
    raise ValueError(f"Unknown sink type: {cfg.type!r}")
