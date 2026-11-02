from __future__ import annotations

import math
import random

from .config import RobotParams
from .models import Telemetry, iso_utc

CHARGER = (0.0, 0.0)


class Robot:
    """Physics-flavoured warehouse AMR. Knows nothing about publishing or behaviors."""

    def __init__(self, robot_id: str, params: RobotParams, rng: random.Random):
        self.id = robot_id
        self.params = params
        self.rng = rng
        self.x = rng.uniform(0, params.area_width)
        self.y = rng.uniform(0, params.area_height)
        self.heading = rng.uniform(-math.pi, math.pi)
        self.speed = 0.0
        self.battery = (
            params.initial_battery_pct
            if params.initial_battery_pct is not None
            else rng.uniform(60, 100)
        )
        self.temp = params.ambient_temp
        self.wear = rng.uniform(0.0, 0.05)
        self.mode = "moving"
        self.cruise = rng.uniform(0.6, 1.0)
        self.target = self._new_target()
        self.seq = 0

    def _new_target(self) -> tuple[float, float]:
        p = self.params
        return self.rng.uniform(0, p.area_width), self.rng.uniform(0, p.area_height)

    def _noise(self) -> float:
        return self.rng.gauss(0.0, self.params.noise_std)

    def step(self, dt: float, epoch_s: float) -> Telemetry:
        p = self.params

        if self.mode == "charging":
            self.speed = 0.0
            self.battery += p.charge_rate * dt
            if self.battery >= p.resume_battery_pct:
                self.mode, self.target = "moving", self._new_target()
        else:
            if self.mode == "moving" and self.battery <= p.low_battery_pct:
                self.mode, self.target = "returning", CHARGER
            desired = p.max_speed if self.mode == "returning" else p.max_speed * self.cruise
            delta = max(-p.acceleration * dt, min(p.acceleration * dt, desired - self.speed))
            self.speed = max(0.0, self.speed + delta)

            dx, dy = self.target[0] - self.x, self.target[1] - self.y
            dist = math.hypot(dx, dy)
            if dist < 0.5:
                if self.mode == "returning":
                    self.mode, self.speed = "charging", 0.0
                else:
                    self.target = self._new_target()
            else:
                self.heading = math.atan2(dy, dx)
                travel = min(self.speed * dt, dist)
                self.x += math.cos(self.heading) * travel
                self.y += math.sin(self.heading) * travel
            self.battery -= (p.battery_drain_idle + p.battery_drain_per_speed * self.speed) * dt

        self.battery = max(0.0, min(100.0, self.battery))
        self.wear = min(1.0, self.wear + p.wear_rate * dt)

        current = (p.motor_idle_current + p.motor_current_per_speed * self.speed) * (1 + 1.5 * self.wear)
        current = max(0.0, current + self._noise())
        self.temp += (p.heat_coeff * current**2 - p.cool_coeff * (self.temp - p.ambient_temp)) * dt
        self.temp += self._noise() * 0.1

        code = 0
        if self.temp > p.overtemp_threshold:
            code = 201
        elif self.rng.random() < p.fault_prob * dt:
            code = self.rng.choice([101, 102])
        elif self.battery < 5.0:
            code = 301

        status = "fault" if code in (101, 102, 201) else self.mode
        self.seq += 1
        return Telemetry(
            robot_id=self.id,
            seq=self.seq,
            ts=iso_utc(epoch_s),
            x=round(self.x, 3),
            y=round(self.y, 3),
            heading_deg=round(math.degrees(self.heading), 2),
            speed=round(self.speed, 3),
            battery_pct=round(self.battery, 3),
            motor_current=round(current, 3),
            motor_temp=round(self.temp, 3),
            error_code=code,
            status=status,
        )
