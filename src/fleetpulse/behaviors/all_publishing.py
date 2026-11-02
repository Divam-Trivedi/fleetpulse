from pydantic import BaseModel

from .base import Behavior, register_behavior


class AllPublishingParams(BaseModel):
    pass


@register_behavior
class AllPublishing(Behavior):
    """Every robot publishes on every tick."""

    name = "all_publishing"
    params_model = AllPublishingParams

    def register(self, robot_id: str, now: float) -> None:
        pass

    def is_active(self, robot_id: str, now: float) -> bool:
        return True
