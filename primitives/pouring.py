"""Pour team: encapsulate grasp, transport, pour, return, and release."""
from .types import Context, Target

IMPLEMENTED = set()


async def pour(ctx: Context, *, source: Target, target: Target) -> dict:
    """Start/end empty-handed; pour source into target and return source to its place.

    Dispatch coffee/coconut_water to your object-specific implementation here.
    Calibrated amount/dwell belongs to the implementation/config, not the planner.
    """
    raise NotImplementedError('Team supplies pour')
