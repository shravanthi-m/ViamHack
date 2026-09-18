"""Contract demonstration only: fabricated observations, no simulation or Viam calls."""
from . import camera


async def invoke(name, ctx, args):
    if name == 'localize':
        return dict(object_id=args['object_id'], frame=ctx.config['frame'],
                    pose=dict(x=0, y=0, z=100, o_x=0, o_y=0, o_z=1, theta=0))
    if name == 'capture':
        # Shaped like a real capture, but nothing was photographed and no file written.
        return dict(view=args['view'], camera=camera.resolve(ctx.config, args['view']),
                    captured_at=None, images=[], mock=True)
    return {'mock': True, 'primitive': name}
