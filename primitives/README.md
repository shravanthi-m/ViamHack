# Human-owned tools

Implement these functions; the planner and runtime are already wired to them.
The initial bodies raise `NotImplementedError`. `mock.py` is used only by offline
runs and is never a fallback for physical execution.

| Function | Module | Contract |
| --- | --- | --- |
| `localize(ctx, object_id=...)` | `localization.py` | Return `Target` in configured frame; raise when uncertain |
| `go_to_origin(ctx)` | `motion.py` | Plan back to the taught origin pose (implemented) |
| `go_to_pose(ctx, pose=...)` | `motion.py` | Move to an explicitly supplied task-frame `x`/`y`/`z` and `yaw` (implemented) |
| `pour(ctx, source=..., target=...)` | `pouring.py` | Empty hand → pick source, pour into target, return source, release → empty hand |
| `pick_up(ctx, target=...)` | `spoon.py` | Empty hand → hold spoon |
| `insert_into(ctx, target=...)` | `spoon.py` | Held spoon → inserted in cup, still held |
| `stir(ctx, target=..., duration_s=...)` | `spoon.py` | Inserted spoon → stir → still inserted and held |
| `place_back(ctx, target=...)` | `spoon.py` | Withdraw spoon, return to original spoon target, release |

`pour` can dispatch on `source['object_id']` to separately implemented coffee and
coconut-water routines. The initial planner does not select amounts or tilt angles.
Do not expose robot-driver details to the planner to avoid implementing a composite
primitive. Team-owned tuning can live in an extra top-level `primitive_settings`
section of the local config, which the responsible primitive must validate.
`motion.py` reads `primitive_settings.motion` for `position_tolerance_mm` and
`orientation_tolerance_deg` (both 5), `joint_tolerance_deg` (2), `speed_deg_s`
(unset, which leaves the arm module's own speed alone), `origin_pose`, and
`origin_joints_deg`.

`go_to_pose` and `go_to_origin` are both thin abstractions over the Viam motion
service, so obstacle avoidance comes from the machine's configured frame system and
geometry. Home first and every commanded pose is approached from one known state.

`go_to_origin` needs `origin_pose`, a full taught pose that keeps whatever
orientation it was taught at. A pose cannot be computed from joint angles without
going there, so `teach_origin` visits `origin_joints_deg` once, measures the pose,
and `main.py --teach-origin --execute` stores it in the gitignored
`config/local.json`. That single joint move is the one motion here the service does
not plan; everything afterwards is planned.

Every function is `async`, gets a `Context(config, robot)`, and returns a JSON dict.
Actions may return `{}` after verifying completion; failures must raise an
exception. Returning `False`, `None`, or `{'success': false}` is not the failure
protocol. Catch failed SDK booleans and raise inside the primitive. The runtime
stops at the first exception, requests StopAll, and does not auto-release/retry.

`Target` is defined in `types.py`:

```python
{
    'object_id': 'cup',
    'frame': 'world',  # must equal ctx.config['frame']
    'pose': {'x': ..., 'y': ..., 'z': ...,
             'o_x': ..., 'o_y': ..., 'o_z': ..., 'theta': ...}
}
```

`go_to_pose` is the exception to that shape: the planner supplies only

```python
{'x': ..., 'y': ..., 'z': ...,  # millimeters in ctx.config['frame']
 'yaw': ...}                    # degrees about the frame's Z axis
```

because the tool points straight down for every pose the planner can author. The
primitive turns that into the orientation vector `(0, 0, -1)` with `theta=yaw` and
hands it to the Viam motion service, which owns planning, frame transforms, and
configured collision geometry. A pose that needs a different tool axis belongs to
a `Target` and its own primitive, not to `go_to_pose`.

Translation is **millimeters**, orientation is a Viam orientation vector with
theta in **degrees**, not Euler angles. Agree on object-specific manipulation
anchors between perception and manipulation teams; localization must transform
camera coordinates into the configured task frame. Targets are fresh only for the
scene they were observed in. If the scene changes, localize again. Primitives must
check held-object state, grasp/insertion feasibility, and other physical
preconditions; the executor checks shape, reference order, timeouts, and endpoint
bounds but does not model physical state or whole-arm collision clearance.

The agent uses your reported observations to gate the next phase, diagnose faults,
and refine skills. Return the measured postconditions and evidence references your
implementation can support, and document their meaning. An empty result is still
valid at the dispatch layer but supplies no outcome evidence. The agent must then
obtain observations separately before admitting a dependent phase or labeling a
physical trial successful. See [task gating](../agents/task_gating.md) and the
[skill library](../skill_library/README.md).

Plans pass `{"$ref": "cup"}` as a target. The executor resolves it to the **whole**
result of the earlier localization step before calling you. Do not parse `$ref`
or plan JSON inside a primitive.

To enable one implementation, change the set in its module, for example:

```python
IMPLEMENTED = {'localize'}
```

Then `python -m runtime tools` reports it ready. Readiness is a team's declaration,
not certification. Physical runs require **every** planned tool to be enabled
before connection. Mock runs never invoke team code. The serial executor owns one
robot connection; primitives must not close it or leave background motion tasks.

Start with `Arm.from_robot(ctx.robot, ctx.config['resources']['arm'])` (or the
appropriate service/component). Reuse Viam motion planning, frames, and configured
geometry. Do not override torque/simulation settings as a side effect of executing
a language instruction. Share hardware access by coordinating physical trials;
the scaffold has no cross-process robot lock.
