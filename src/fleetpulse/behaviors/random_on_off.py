from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from .base import Behavior, register_behavior


class RandomOnOffParams(BaseModel):
    on_s: tuple[float, float] = (10.0, 40.0)      # sim seconds powered on
    off_s: tuple[float, float] = (5.0, 20.0)      # sim seconds powered off
    start_on_prob: float = Field(0.7, ge=0, le=1)


@dataclass
class _State:
    on: bool
    next_toggle: float


@register_behavior
class RandomOnOff(Behavior):
    """Each robot independently powers on and off with random dwell times."""

    name = "random_on_off"
    params_model = RandomOnOffParams

    def __init__(self, params, rng):
        super().__init__(params, rng)
        self._state: dict[str, _State] = {}

    def _dwell(self, on: bool) -> float:
        lo, hi = self.params.on_s if on else self.params.off_s
        return self.rng.uniform(lo, hi)

    def register(self, robot_id: str, now: float) -> None:
        on = self.rng.random() < self.params.start_on_prob
        self._state[robot_id] = _State(on, now + self._dwell(on))

    def unregister(self, robot_id: str) -> None:
        self._state.pop(robot_id, None)

    def is_active(self, robot_id: str, now: float) -> bool:
        s = self._state[robot_id]
        while now >= s.next_toggle:
            s.on = not s.on
            s.next_toggle += self._dwell(s.on)
        return s.on

    def role(self, robot_id: str) -> str:
        return "on" if self._state[robot_id].on else "off"
