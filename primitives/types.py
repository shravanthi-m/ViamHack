"""The only shared types primitive authors need. Poses use mm and Viam degrees."""
from dataclasses import dataclass
from typing import Any, TypedDict


class Pose(TypedDict):
    x: float
    y: float
    z: float
    o_x: float
    o_y: float
    o_z: float
    theta: float


class Target(TypedDict):
    object_id: str
    frame: str
    pose: Pose


@dataclass
class Context:
    config: dict
    robot: Any = None  # Connected Viam RobotClient only in physical execution.
