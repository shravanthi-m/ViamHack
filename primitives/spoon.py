"""Spoon team: one independently enabled handler per primitive."""
from .types import Context, Target

IMPLEMENTED = set()


async def pick_up(ctx: Context, *, target: Target) -> dict:
    """Start empty-handed; finish holding the target, or raise."""
    raise NotImplementedError('Team supplies pick_up')


async def insert_into(ctx: Context, *, target: Target) -> dict:
    """Insert the held spoon into the localized cup; retain the grasp."""
    raise NotImplementedError('Team supplies insert_into')


async def stir(ctx: Context, *, target: Target, duration_s: float) -> dict:
    """Stir with the inserted, held spoon; finish still holding it in the cup."""
    raise NotImplementedError('Team supplies stir')


async def place_back(ctx: Context, *, target: Target) -> dict:
    """Withdraw the held spoon, return to its original target, and release."""
    raise NotImplementedError('Team supplies place_back')
