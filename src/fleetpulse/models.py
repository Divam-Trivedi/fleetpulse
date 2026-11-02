from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class Telemetry:
    robot_id: str
    seq: int
    ts: str
    x: float
    y: float
    heading_deg: float
    speed: float
    battery_pct: float
    motor_current: float
    motor_temp: float
    error_code: int          # 0 none, 101 lidar dropout, 102 wheel slip, 201 overtemp, 301 low battery
    status: str              # moving | returning | charging | fault

    def to_dict(self) -> dict:
        return asdict(self)


def iso_utc(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat(timespec="milliseconds")
