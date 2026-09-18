"""Motion team: use configured Viam planning and collision geometry."""
from .types import Context, Pose

IMPLEMENTED = set()


async def go_to_pose(ctx: Context, *, pose: Pose) -> dict:
    """Move to a pose in ctx.config['frame']; verify completion or raise."""
    raise NotImplementedError('Team supplies go_to_pose')
