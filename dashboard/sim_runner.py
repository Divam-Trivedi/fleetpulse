"""Runs FleetController in a background thread so the dashboard can start and stop it and call its
control API (set_behavior, scale_fleet, update_params, reset_robot) while it is running."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Callable

from fleetpulse.config import FleetConfig, load_config
from fleetpulse.controller import FleetController

SINK_CHOICES = {
    "None (discard)": ("null", None),
    "Local file (JSONL)": ("jsonl", None),
    "Firehose: JSON stream": ("firehose", "fleetpulse-telemetry"),
    "Firehose: Parquet stream": ("firehose", "fleetpulse-telemetry-parquet"),
}


def build_config(base_path: str, behavior: str, robots: int, time_scale: float, sink_choice: str,
                 region: str = "us-west-2", profile: str | None = None,
                 behavior_params: dict | None = None) -> FleetConfig:
    cfg = load_config(base_path)
    cfg.fleet.size = robots
    cfg.time_scale = time_scale
    cfg.behavior.name = behavior
    cfg.behavior.params = behavior_params or {}
    kind, stream = SINK_CHOICES[sink_choice]
    cfg.sink.type = kind
    cfg.sink.path = "data/dashboard_run.jsonl"
    if stream:
        cfg.sink.stream_name, cfg.sink.region, cfg.sink.profile = stream, region, profile or None
    return cfg


class SimRunner:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ctl: FleetController | None = None
        self._ready = threading.Event()
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._ctl is not None)

    def start(self, config: FleetConfig) -> None:
        if self.running:
            raise RuntimeError("Simulator is already running")
        self.error, self._ctl = None, None
        self._ready.clear()
        self._thread = threading.Thread(target=self._main, args=(config,), daemon=True, name="fleetpulse-sim")
        self._thread.start()
        self._ready.wait(timeout=10)
        if self.error:
            raise RuntimeError(self.error)

    def _main(self, config: FleetConfig) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._ctl = FleetController(config)          # builds the sink (may raise, e.g. unknown AWS profile)
            self._ready.set()
            loop.run_until_complete(self._ctl.run(None))  # until stop() is called
        except Exception as exc:                          # surfaced to the UI instead of dying silently
            self.error = f"{type(exc).__name__}: {exc}"
            self._ctl = None
            self._ready.set()
        finally:
            loop.close()

    def with_controller(self, fn: Callable[[FleetController], Any], timeout: float = 5.0) -> Any:
        """Run fn(controller) on the simulator's own event loop thread and return its result."""
        if not self.running or self._loop is None:
            raise RuntimeError("Simulator is not running")
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def _do() -> None:
            try:
                fut.set_result(fn(self._ctl))
            except Exception as exc:
                fut.set_exception(exc)

        self._loop.call_soon_threadsafe(_do)
        return fut.result(timeout=timeout)

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self.with_controller(lambda c: getattr(c, name)(*args, **kwargs))

    def status(self) -> dict:
        def grab(c: FleetController) -> dict:
            s = c.get_status()
            stats = getattr(c.sink, "stats", None)
            s["sink_stats"] = dict(stats) if stats else None
            s["sink_type"] = c.config.sink.type
            return s

        return self.with_controller(grab)

    def stop(self) -> None:
        if self._ctl and self._loop and self._thread and self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(self._ctl.stop(), self._loop).result(timeout=15)
            self._thread.join(timeout=15)
