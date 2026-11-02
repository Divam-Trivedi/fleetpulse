from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .behaviors import Behavior, create_behavior
from .config import FleetConfig, RobotParams
from .robot import Robot
from .sinks import Sink, create_sink


class SimClock:
    """Simulated time. time_scale=10 means 10 sim seconds per real second."""

    def __init__(self, time_scale: float):
        self.time_scale = time_scale
        self._t0 = time.monotonic()
        self._epoch0 = time.time()

    def start(self) -> None:
        self._t0, self._epoch0 = time.monotonic(), time.time()

    def now(self) -> float:
        return (time.monotonic() - self._t0) * self.time_scale

    def epoch(self) -> float:
        return self._epoch0 + self.now()


@dataclass
class _Runtime:
    robot: Robot
    task: asyncio.Task | None = None
    active: bool = False
    published: int = 0
    last: dict | None = None


class FleetController:
    """Single control surface for the fleet. The CLI uses it now; a dashboard API will later."""

    def __init__(self, config: FleetConfig, sink: Sink | None = None):
        self.config = config
        self.sink = sink or create_sink(config.sink)
        self.clock = SimClock(config.time_scale)
        self._master = random.Random(config.seed)
        self._rt: dict[str, _Runtime] = {}
        self._next_idx = 0
        self._running = False
        self._stop = asyncio.Event()
        self._total = 0
        self.run_id = uuid.uuid4().hex[:8]
        self._behavior: Behavior = self._make_behavior(config.behavior.name, config.behavior.params)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self.clock.start()
        self._running = True
        for _ in range(self.config.fleet.size):
            self._add_robot()

    async def stop(self) -> None:
        self._running = False
        self._stop.set()
        tasks = [rt.task for rt in self._rt.values() if rt.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.sink.close()

    async def run(self, duration_s: float | None = None) -> None:
        """Run for `duration_s` simulated seconds, or until stop() is called."""
        await self.start()
        try:
            if duration_s is None:
                await self._stop.wait()
            else:
                await asyncio.sleep(duration_s / self.config.time_scale)
        finally:
            await self.stop()

    # ---------- runtime control ----------
    def set_behavior(self, name: str, **params: Any) -> None:
        self._behavior = self._make_behavior(name, params)
        now = self.clock.now()
        for rid in self._rt:
            self._behavior.register(rid, now)
        self.config.behavior.name, self.config.behavior.params = name, params

    def scale_fleet(self, n: int) -> None:
        if n < 0:
            raise ValueError("Fleet size must be >= 0")
        while len(self._rt) < n:
            self._add_robot()
        while len(self._rt) > n:
            self._remove_robot(next(reversed(self._rt)))
        self.config.fleet.size = n

    def update_params(self, robot_id: str | None = None, **changes: Any) -> None:
        """Change params live for one robot, or the whole fleet when robot_id is None."""
        if robot_id is None:
            merged = {**self.config.fleet.defaults.model_dump(), **changes}
            self.config.fleet.defaults = RobotParams.model_validate(merged)
            targets = list(self._rt.values())
        else:
            if robot_id not in self._rt:
                raise KeyError(robot_id)
            self.config.fleet.overrides.setdefault(robot_id, {}).update(changes)
            targets = [self._rt[robot_id]]
        for rt in targets:
            merged = {**rt.robot.params.model_dump(), **changes}
            rt.robot.params = RobotParams.model_validate(merged)

    def reset_robot(self, robot_id: str) -> None:
        self._behavior.reset(robot_id, self.clock.now())

    def get_status(self) -> dict:
        robots = [
            {
                "id": rid,
                "active": rt.active,
                "role": self._behavior.role(rid),
                "published": rt.published,
                "params": rt.robot.params.model_dump(),
                "last": rt.last,
            }
            for rid, rt in self._rt.items()
        ]
        return {
            "run_id": self.run_id,
            "running": self._running,
            "sim_time_s": round(self.clock.now(), 2),
            "time_scale": self.config.time_scale,
            "behavior": {"name": self._behavior.name, "params": self._behavior.params.model_dump()},
            "fleet_size": len(self._rt),
            "active_count": sum(r["active"] for r in robots),
            "total_published": self._total,
            "robots": robots,
        }

    # ---------- internals ----------
    def _make_behavior(self, name: str, params: dict) -> Behavior:
        return create_behavior(name, params, random.Random(self._master.randrange(2**32)))

    def _add_robot(self) -> None:
        rid = self.config.fleet.robot_id(self._next_idx)
        self._next_idx += 1
        robot = Robot(rid, self.config.fleet.params_for(rid), random.Random(self._master.randrange(2**32)))
        rt = _Runtime(robot)
        self._rt[rid] = rt
        self._behavior.register(rid, self.clock.now())
        if self._running:
            rt.task = asyncio.create_task(self._robot_loop(rt), name=rid)

    def _remove_robot(self, rid: str) -> None:
        rt = self._rt.pop(rid)
        if rt.task:
            rt.task.cancel()
        self._behavior.unregister(rid)

    async def _robot_loop(self, rt: _Runtime) -> None:
        robot, scale = rt.robot, self.clock.time_scale
        await asyncio.sleep(random.random() / robot.params.publish_hz / scale)   # de-sync start
        while True:
            period = 1.0 / robot.params.publish_hz
            await asyncio.sleep(period / scale)
            rt.active = self._behavior.is_active(robot.id, self.clock.now())
            if not rt.active:
                continue
            record = robot.step(period, self.clock.epoch()).to_dict()
            record["run_id"] = self.run_id
            await self.sink.write(record)
            rt.published += 1
            rt.last = record
            self._total += 1
