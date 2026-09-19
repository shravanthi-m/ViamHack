"""Teammate integration: async squeeze(ctx) -> dict, stationary hold-to-hold.

Bottle is already held over the cup. Do not transport, release, or reconnect.
Wire the reviewed action-only function here before setting READY=True.
"""
READY = False


async def squeeze(ctx):
    raise NotImplementedError('Action-only squeeze is not integrated yet')
