# Task policy and Viam machine configuration

Keep two sources of configuration with distinct responsibilities:

| Setting | Source |
| --- | --- |
| Hardware models, drivers, connection attributes, simulation/fake model selection | Viam machine config |
| Frame tree, TCP transforms, configured collision geometry | Viam machine config |
| Torque/force/speed capabilities and hardware defaults | Installed component/module and its Viam settings |
| Credentials/address | Local `.env` / environment variables |
| Task roles → resource names, working frame | `config/demo.json` or full `config/local.json` |
| Allowed objects, step/time limits, maximum stir duration | Local task config |
| Task workspace, derived from the configured geometry | Local task config |
| Object-specific grasp/pour tuning | Team-owned config consumed/validated by its primitive |
| Camera-to-frame hand-eye calibration (homography) | Measured artifact under `config/`, named by the local task config |

Viam already stores component model, attributes, and frame information in its
[machine configuration](https://docs.viam.com/hardware/machine-configuration/).
The demo should reference that hardware rather than reproduce its driver settings.

`python -m runtime inspect` connects read-only and prints `resource_names` and
`get_frame_system_config()` results. This lets you copy the correct bindings and
see configured frames/geometry. It does not edit settings, capture camera images,
probe torque extensions, or automatically choose between multiple arms/cameras.
These runtime APIs are documented in Viam's
[machine management API](https://docs.viam.com/reference/apis/robot/).

Those APIs do **not** provide a universal torque/simulation settings object. For
arbitrary component attributes, inspect/export the machine's CONFIGURE JSON or use
the appropriate Viam app API with suitable permissions. Model-specific force
readback belongs in a verified primitive adapter. Seeing a torque control in a UI
does not imply that every gripper implements the same SDK command or force units.

`python -m runtime calibrate` turns that configured geometry into the task
workspace. It connects read-only, reads the same frame system `inspect` prints, and
keeps every face of `workspace_mm` clear of the fixed obstacles the machine already
publishes -- the table, the ceiling, the walls. Nothing is measured by hand:

- An obstacle is a frame bolted straight to the task frame with no joints. Anything
  that moves, and anything bound to a configured arm/gripper/camera, is left out and
  listed as skipped, so nothing is silently ignored.
- Each obstacle is grown by the room the tool needs before it bounds a face. That
  extent is read from the configured gripper geometry: how far it reaches above the
  commanded point, how far below, and its sideways radius, since `go_to_pose` points
  the tool down and leaves yaw free. `calibration.margin_mm` is added on top.
- Faces no obstacle bounds fall back to `calibration.reach_mm`, a declared cap on
  tool distance from the arm base. Without it the arm's own link offsets give a
  loose upper bound. The cap is policy, not a reachability promise: the motion
  service still plans, and still refuses poses this arm cannot hold.
- Bounds are rounded inwards, and the written config records the obstacles, the
  tool extent, and which obstacle set each face under `calibration.derived`.

Leave `calibrated=false` and bounds null until they are derived and reviewed; the
executor refuses physical runs in that state. With `--execute`, it checks supplied
poses and localization results against those bounds. It cannot inspect internal
primitive paths; their authors must enforce those bounds and use Viam's collision
planning too. A human still owns the limits: `calibrate` alone writes nothing, and
`--write` saves what the machine's geometry implies, never a wider box to make a
skill pass.

## Automatic closure and object settings

For the optional taught-pickup vision adapter and its measured per-object profiles,
see [minimal vision demo](vision_demo.md). It uses `primitive_settings.replay_vision`
and does not change the generic `localize` calibration contract.

For this station's UFactory module, merge this block into the existing
`primitive_settings` in ignored `config/local.json`:

```json
"gripper": {
  "force_control": "ufactory_atomic",
  "object_force_percent": {
    "coconut_water": 10,
    "pitcher": 20
  }
}
```

These are controller percentages supplied by the operator, not measured newtons.
Add entries using configured object IDs when the team supplies their settings.
Automatic replay requires a setting for every source before either stage can run;
it uses this map instead of the saved teaching force. It preserves the historical
recording and records the selected setting in run evidence. Missing, nonnumeric,
fractional, or out-of-limit settings fail offline. The existing force ceiling
remains 30% by default; do not raise it to accommodate a failing grasp.

The explicit adapter selects the station's `grab_with_torque` extension. It reads
and preserves the current gripper speed, requires an empty hand, sends force and
closure in one command, and requires holding afterward. It does not use standalone
torque setters or fall back to ordinary Grab. An RPC acknowledgement is not force
readback: returned evidence marks `force_readback: false`. Supported module routing
and prior coconut closure were inspected in the station's live-run evidence; this
integration still needs physical rehearsal, especially for the pitcher.

Without this opt-in, the existing torque-readback adapter and manual closure for
operator-entered recordings remain unchanged. Automatic closure removes the manual
gripper operation; placement, intended-object, support/release and outcome gates
remain supervised. A timeout, unsupported command or failed holding check stops
the replay without retry or release. Rebuild and rehearse frozen demo packs after
changing these settings or primitive code.

`workspace_mm` says where the arm may go; it says nothing about where an object
*is*. That second question is a separate calibration, per camera view, and the two
are measured by different means: the workspace comes from the machine's own frame
system, while the camera-to-frame mapping has to be measured against the world.

The overhead mapping is a 3x3 planar homography in
`config/overhead_homography.json` -- `H`, the frame and units it was measured in,
and its reported error -- which
`primitive_settings.localization.homographies` names by camera view. It converts an
image pixel to task-frame `x`/`y` in millimetres on the calibrated surface, so
`localization.py` can turn an OpenCV detection into a `go_to_pose` argument. Keeping
it in its own file means re-running the calibration procedure replaces one artifact
and touches no policy.

It carries the limits of what it is. It is planar, so it supplies no `z`: height
comes from `primitive_settings.localization.hover_z_mm` or the caller, and has to
clear the tallest object on the surface rather than the surface. It is tied to the
image resolution it was measured at, so a detector must report pixels in that same
resolution. And it is only as accurate as the measurement -- the current overhead
fit reports 27 mm mean and 68 mm maximum error, which every conversion returns
alongside the position so a gate can weigh it rather than assume it.

Use `cp config/demo.json config/local.json`, then edit that full file. Select it
explicitly using `python -m runtime --config config/local.json ...`. There is no
hidden merge or automatic retrieval of hardware settings, and calibration only ever
runs when you ask for it.
The example's resource names come from the earlier station config and should be
verified with `inspect` on your machine. `--execute` invokes the configured Viam
machine, which may itself use a simulated model; offline mock mode never connects
to either real or simulated hardware.

The earlier learning experiment keeps its **own** station/profile schema under
`experiments/skill_learning/`. Do not feed it the new demo config or vice versa.
