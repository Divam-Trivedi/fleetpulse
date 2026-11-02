from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class RobotParams(BaseModel):
    """Per-robot tunables. Every field can be overridden per robot or live."""

    publish_hz: float = Field(1.0, gt=0)
    max_speed: float = Field(1.5, gt=0)            # m/s
    acceleration: float = Field(1.0, gt=0)         # m/s^2
    area_width: float = Field(60.0, gt=0)          # m
    area_height: float = Field(40.0, gt=0)         # m
    initial_battery_pct: float | None = Field(None, ge=0, le=100)
    battery_drain_idle: float = 0.002              # %/s
    battery_drain_per_speed: float = 0.004         # %/s per m/s
    charge_rate: float = 0.5                       # %/s
    low_battery_pct: float = 20.0
    resume_battery_pct: float = 95.0
    motor_idle_current: float = 1.0                # A
    motor_current_per_speed: float = 2.0           # A per m/s
    heat_coeff: float = 0.025
    cool_coeff: float = 0.02
    ambient_temp: float = 25.0                     # C
    overtemp_threshold: float = 85.0               # C
    wear_rate: float = 0.00002                     # wear fraction per sim second
    fault_prob: float = Field(0.002, ge=0)         # per sim second
    noise_std: float = Field(0.05, ge=0)


class FleetSettings(BaseModel):
    size: int = Field(10, ge=0)
    id_prefix: str = "amr"
    defaults: RobotParams = Field(default_factory=RobotParams)
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def robot_id(self, index: int) -> str:
        return f"{self.id_prefix}-{index:03d}"

    def params_for(self, robot_id: str) -> RobotParams:
        merged = {**self.defaults.model_dump(), **self.overrides.get(robot_id, {})}
        return RobotParams.model_validate(merged)


class BehaviorSettings(BaseModel):
    name: str = "all_publishing"
    params: dict[str, Any] = Field(default_factory=dict)


class SinkSettings(BaseModel):
    type: str = "stdout"                      # stdout | jsonl | null | firehose
    path: str = "data/telemetry.jsonl"        # jsonl only
    stream_name: str | None = None            # firehose only
    region: str | None = None
    profile: str | None = None                # AWS CLI profile
    batch_size: int = Field(500, ge=1, le=500)
    flush_interval_s: float = Field(1.0, gt=0)


class FleetConfig(BaseModel):
    seed: int | None = None
    time_scale: float = Field(1.0, gt=0)
    fleet: FleetSettings = Field(default_factory=FleetSettings)
    behavior: BehaviorSettings = Field(default_factory=BehaviorSettings)
    sink: SinkSettings = Field(default_factory=SinkSettings)


def load_config(path: str | Path | None = None) -> FleetConfig:
    if path is None:
        return FleetConfig()
    with open(path) as f:
        return FleetConfig.model_validate(yaml.safe_load(f) or {})
