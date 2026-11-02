from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from .base import Behavior, register_behavior

ROLES = ("continuous", "random", "abrupt")


class MixedFleetParams(BaseModel):
    continuous_pct: float = Field(60, ge=0, le=100)
    random_pct: float = Field(30, ge=0, le=100)
    abrupt_pct: float = Field(10, ge=0, le=100)
    random_publish_prob: float = Field(0.5, ge=0, le=1)       # per tick, for 'random' role
    abrupt_after_s: tuple[float, float] = (30.0, 120.0)       # sim seconds until sudden death

    @model_validator(mode="after")
    def _sum_to_100(self):
        total = self.continuous_pct + self.random_pct + self.abrupt_pct
        if abs(total - 100) > 1e-6:
            raise ValueError(f"Percentages must sum to 100, got {total}")
        return self


@dataclass
class _State:
    role: str
    stop_at: float | None = None


@register_behavior
class MixedFleet(Behavior):
    """Percentage split: some robots run continuously, some publish randomly,
    some stop abruptly (silently, until reset)."""

    name = "mixed_fleet"
    params_model = MixedFleetParams

    def __init__(self, params, rng):
        super().__init__(params, rng)
        self._state: dict[str, _State] = {}

    def _target_fractions(self) -> dict[str, float]:
        p = self.params
        return {
            "continuous": p.continuous_pct / 100,
            "random": p.random_pct / 100,
            "abrupt": p.abrupt_pct / 100,
        }

    def _pick_role(self) -> str:
        """Pick the role furthest below its target share, so percentages stay
        exact as robots are added or removed."""
        counts = {r: 0 for r in ROLES}
        for s in self._state.values():
            counts[s.role] += 1
        n = len(self._state) + 1
        fr = self._target_fractions()
        deficit = {r: fr[r] * n - counts[r] for r in ROLES}
        best = max(deficit.values())
        return self.rng.choice([r for r in ROLES if deficit[r] == best])

    def _schedule(self, state: _State, now: float) -> None:
        if state.role == "abrupt":
            lo, hi = self.params.abrupt_after_s
            state.stop_at = now + self.rng.uniform(lo, hi)

    def register(self, robot_id: str, now: float) -> None:
        state = _State(self._pick_role())
        self._schedule(state, now)
        self._state[robot_id] = state

    def unregister(self, robot_id: str) -> None:
        self._state.pop(robot_id, None)

    def is_active(self, robot_id: str, now: float) -> bool:
        s = self._state[robot_id]
        if s.role == "continuous":
            return True
        if s.role == "random":
            return self.rng.random() < self.params.random_publish_prob
        return now < s.stop_at

    def reset(self, robot_id: str, now: float) -> None:
        s = self._state[robot_id]
        self._schedule(s, now)

    def role(self, robot_id: str) -> str:
        s = self._state[robot_id]
        if s.role == "abrupt" and s.stop_at is not None:
            return "abrupt"
        return s.role
