"""Perception team: camera/detection/frame transforms belong here."""
from .types import Context, Target

IMPLEMENTED = set()


async def localize(ctx: Context, *, object_id: str) -> Target:
    """Return the object's manipulation target in ctx.config['frame']; fail if uncertain."""
    raise NotImplementedError('Team supplies localization')
