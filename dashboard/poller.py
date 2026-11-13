"""Background polling so the screen never waits on the network (which is what made the page fade every few seconds).

A daemon thread collects AWS and Snowflake status (free metadata calls) and, when asked or on a timer, refreshes the
robot tables (real Snowflake queries). The page only reads the latest finished result, which is instant. The thread
stops by itself if nobody is looking, so it never polls for an abandoned browser tab."""
from __future__ import annotations

import threading
import time
from typing import Callable

import pandas as pd

from . import collect, data, infra, status


class Poller:
    IDLE_STOP_S = 90.0

    def __init__(self, interval: float = 15.0, aws_factory: Callable | None = None) -> None:
        self.interval = interval
        self.aws_factory = aws_factory or infra.aws_clients
        self._lock = threading.RLock()
        self._sf_lock = threading.Lock()                 # serialises use of the poller's own Snowflake session
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._cfg: dict = {"profile": None, "region": "us-west-2", "sf_factory": None, "runner": None, "auto": 0, "paused": False}
        self._aws = None
        self._aws_key = None
        self._sf = None
        self._status: dict | None = None
        self._prev: dict | None = None
        self._data: dict = {}
        self._want_run: str | None = None
        self._touched = time.time()
        self.sf_error: str | None = None

    # ---- configuration (called on every page run; cheap) ----
    def configure(self, profile, region, sf_factory, runner, auto_seconds: int, paused: bool) -> None:
        with self._lock:
            self._cfg = {"profile": profile, "region": region, "sf_factory": sf_factory, "runner": runner, "auto": auto_seconds, "paused": paused}
        if sf_factory is None and self._sf is not None:
            with self._sf_lock:
                try:
                    self._sf.close()
                except Exception:
                    pass
                self._sf, self._data = None, {}

    def touch(self) -> None:
        self._touched = time.time()
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, daemon=True, name="fleetpulse-poller")
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    # ---- reading results (instant) ----
    def latest(self) -> dict | None:
        with self._lock:
            return self._status

    def data(self) -> dict:
        with self._lock:
            return dict(self._data)

    def aws(self):
        return self._aws

    # ---- work ----
    def _ensure_aws(self):
        key = (self._cfg["profile"], self._cfg["region"])
        if key != self._aws_key:
            try:
                self._aws = self.aws_factory(self._cfg["profile"] or None, self._cfg["region"])
            except Exception:
                self._aws = None
            self._aws_key = key
        if self._aws is not None:
            try:
                self._aws.sts.get_caller_identity()
            except Exception:
                return None
        return self._aws

    def _ensure_sf(self):
        if self._cfg["sf_factory"] is None:
            return None
        if self._sf is None:
            try:
                self._sf = self._cfg["sf_factory"]()
                self.sf_error = None
            except Exception as exc:
                self.sf_error = str(exc).splitlines()[0][:160]
        return self._sf

    def poll_once(self) -> dict:
        c = self._ensure_aws()
        runner = self._cfg["runner"]
        try:
            sim = runner.status() if runner is not None and runner.running else None
        except Exception:
            sim = None
        aws_snap = None
        fh_df = pd.DataFrame()
        if c is not None:
            try:
                bucket = infra.bucket_name(c)
            except Exception:
                bucket = None
            aws_snap = collect.collect_aws(c, bucket)
            cols = {}
            for s in infra.STREAMS:
                try:
                    cols["Parquet" if s.endswith("parquet") else "JSON"] = pd.Series(dict(infra.firehose_series(c.cw, s, 60)), dtype="float64")
                except Exception:
                    pass
            fh_df = pd.DataFrame(cols).sort_index().fillna(0) if cols else pd.DataFrame()
        sf_snap = None
        with self._sf_lock:
            sf = self._ensure_sf()
            if sf is not None:
                sf_snap = collect.collect_snowflake(sf)
        snap = collect.snapshot(sim, aws_snap, sf_snap)
        items = status.evaluate(snap, self._prev)
        result = {"items": items, "at": time.time(), "firehose": fh_df}
        with self._lock:
            self._prev, self._status = snap, result
        return result

    def refresh_data(self, run_id: str | None = None) -> None:
        """The only place that runs warehouse queries."""
        with self._sf_lock:
            sf = self._ensure_sf()
            if sf is None:
                return
            cache: dict = {}
            try:
                runs = data.runs(sf)
                rid = run_id if run_id in set(runs.run_id) else (runs.run_id.iloc[0] if len(runs) else None)
                cache.update(runs=runs, run_id=rid, dq=data.data_quality(sf))
                if rid:
                    cache.update(robots=data.robots_latest(sf, rid), risk=data.latest_risk(sf, rid), silent=data.silent_robots(sf, rid), fleet=data.fleet_minutely(sf, rid))
                try:
                    cache["alerts"] = data.alert_log(sf)
                except Exception:
                    cache["alerts"] = None
                cache["error"] = None
            except Exception as exc:
                cache["error"] = str(exc).splitlines()[0][:200]
            cache["fetched_at"] = time.time()
        with self._lock:
            self._data = cache
            self._want_run = cache.get("run_id")

    def request_run(self, run_id: str) -> None:
        """The user picked a different run: fetch it on the next cycle."""
        with self._lock:
            self._want_run = run_id
        self._wake.set()

    def _loop(self) -> None:
        while time.time() - self._touched < self.IDLE_STOP_S:
            try:
                self.poll_once()
                c = self._data
                want = self._want_run
                interval = self._cfg["auto"]
                stale = bool(interval) and not self._cfg["paused"] and time.time() - c.get("fetched_at", 0) > interval
                changed = bool(c) and want and want != c.get("run_id")
                if self._cfg["sf_factory"] is not None and (not c or stale or changed):
                    self.refresh_data(want)
            except Exception:
                pass                                         # a failed cycle must never kill the thread
            self._wake.wait(self.interval)
            self._wake.clear()
