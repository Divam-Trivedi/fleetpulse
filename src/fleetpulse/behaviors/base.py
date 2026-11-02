from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

BEHAVIORS: dict[str, type["Behavior"]] = {}


def register_behavior(cls: type["Behavior"]) -> type["Behavior"]:
    BEHAVIORS[cls.name] = cls
    return cls


def create_behavior(name: str, params: dict[str, Any], rng: random.Random) -> "Behavior":
    if name not in BEHAVIORS:
        raise ValueError(f"Unknown behavior {name!r}. Available: {sorted(BEHAVIORS)}")
    cls = BEHAVIORS[name]
    return cls(cls.params_model.model_validate(params), rng)


class Behavior(ABC):
    """Decides, per robot and per tick, whether the robot is active (runs + publishes).

    `now` is simulated seconds since fleet start, so behaviors are testable without sleeping.
    """

    name: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]

    def __init__(self, params: BaseModel, rng: random.Random):
        self.params = params
        self.rng = rng

    @abstractmethod
    def register(self, robot_id: str, now: float) -> None: ...

    def unregister(self, robot_id: str) -> None:
        pass

    @abstractmethod
    def is_active(self, robot_id: str, now: float) -> bool: ...

    def reset(self, robot_id: str, now: float) -> None:
        """Bring a stopped robot back. Default: re-register it."""
        self.unregister(robot_id)
        self.register(robot_id, now)

    def role(self, robot_id: str) -> str:
        return self.name
