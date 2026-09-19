# Taught shake and squeeze workflows

Candidate integration, tested offline. No physical validation is claimed.
Integrated against GitHub main `ea476f2`. The old Documents checkout and its edits
were preserved. Use the fresh checkout at `/Users/shrav/Library/Caches/viamhack-recovery`
and the Python environment at `/Users/shrav/Library/Caches/viamhack-python` while
Documents files are offloaded. Keep your final source in a durable local Git checkout;
the cache location is a working recovery copy, not a backup.

## What is different

For the revised honey **tilt-only** request, use `honey-tilt.md` and
`runtime.honey_tilt`. The squeeze adapter below remains deliberately disabled;
the honey runner does not use it or send squeeze commands.

The runner calls **root `shake_full.shake(ctx, duration_s=...)`**. It does not call
`primitives/shake.py`, `primitives/shake_full.py`, or root `main/run_sequence`.
The root standalone sequence performs its own grip/lift and its `start_pose`
is already elevated. Returning there does not put the object on the table.
Our composition owns pickup and returns to the **taught supported grasp pose**
before release. The root shake implementation itself is unchanged.

Squeeze pose capture and transport composition are implemented. Actual squeezing
is blocked before connection until `runtime/squeeze_action.py` is wired to the
teammate's reviewed stationary action. The existing `primitives/syrup.squeeze`
already does pickup/transport/return and is therefore incompatible with nesting
inside this runner. Its guessed command/fallback must not be silently adopted.

## Setup

Run commands from the fresh repository root:

```sh
cd /Users/shrav/Library/Caches/viamhack-recovery
unset PYTHONPATH PYTHONHOME
source /Users/shrav/Library/Caches/viamhack-python/bin/activate
```

The fresh clone intentionally has no `.env` or `config/local.json`. Supply the
correct machine credentials privately and a reviewed, measured local config.
The tracked demo is a template, not evidence that this setup is calibrated.
Do not reuse screenshot pose values: their frame/tool origin was unverified.
No need to retype x/y/z or compute them from joint angles.

## Record shake poses

```sh
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json init shake
```

Manually position the gripper, exit manual mode and hold still for each command:

```sh
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json teach source_approach
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json teach source_grasp
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json teach source_lift
```

- `source_approach`: open gripper clear of the shaker, aligned for its approach.
- `source_grasp`: open gripper around the supported shaker, ready to close. This
  is also the placement position; the object must be supported before release.
- `source_lift`: a taught clearance position above the source, with the same
  transport orientation, where it is safe to shake. Confirm clearance for the
  object, whole arm, levelling rotation, and full oscillation path.

Teaching uses the motion service's **configured gripper in the configured frame**,
and saves that identity, timestamp and joint reference data with the pose. It
does not move, grip, release, or send StopAll. Joints are not replay targets.
Existing captures are not overwritten; use another book for a fresh teaching.

Edit the book's explicit settings after measuring/testing them on your station:

- `force_percent`: known suitable grip force, within the station gripper limit.
- `travel_speed_deg_s`: approved movement speed, within the existing 30 deg/s limit.
- `placement_speed_deg_s`: approved slower approach/placement speed, no greater
  than travel speed. The runner reapplies it after the shake action.
- `duration_s`: shake duration within the local task limit.
- `settle_s`: pause at the supported placement pose before opening (default 1 s).
- `station_verified`: set true only after validating resource names, frame,
  workspace calibration and the fixed source location.

Set **explicit** reviewed `primitive_settings.shake` in the local config. The
root script's defaults are 10 mm, target 20 Hz, direct mode, and a hardcoded
90 deg/s stroke request. A low frequency does NOT lower that stroke speed.
The runner prints actual resolved settings before execution. Review those with
the shake owner; this integration does not retune their algorithm or limits.

```sh
# Offline: validate all poses/settings and display the sequence.
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json validate

# Also offline without --execute.
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json run

# Physical run: displays the sequence and asks the workspace gate before connecting.
python -m runtime.taught_actions --config config/local.json --book runs/shake-poses.json run --execute
```

Sequence: open empty gripper → approach → grasp → close and verify held → lift →
root shake_full action → lifted clearance pose → slow supported placement →
settle → open and verify release → retreat.

Low speed and arrival checks do not measure surface contact. The taught placement
must actually support the object. Planned paths depend on configured geometry;
the held object is not automatically added to collision geometry. Direct shake
joint interpolation is not certified collision-free by its endpoint plans.
Exceptions/cancellation/timeouts request StopAll and never automatically release
or retry. Connection loss leaves state unknown; inspect it before another run.

## Record squeeze poses

```sh
python -m runtime.taught_actions --config config/local.json --book runs/squeeze-poses.json init squeeze
python -m runtime.taught_actions --config config/local.json --book runs/squeeze-poses.json teach source_approach
python -m runtime.taught_actions --config config/local.json --book runs/squeeze-poses.json teach source_grasp
python -m runtime.taught_actions --config config/local.json --book runs/squeeze-poses.json teach source_lift
python -m runtime.taught_actions --config config/local.json --book runs/squeeze-poses.json teach cup_approach
python -m runtime.taught_actions --config config/local.json --book runs/squeeze-poses.json teach cup_action
```

Source labels have the same meaning for the squeeze bottle. `cup_approach` is a
clear transport waypoint near the cup. `cup_action` is the tool pose that places
the held bottle/nozzle over the cup with the correct orientation and clearance.
Teach using the intended grasp geometry; a bare gripper pose does not account
for an arbitrarily shifted bottle. The source and cup must remain fixed.

After squeeze, the path reverses through cup approach and source lift, then slowly
places at source grasp, releases, and retreats. The teammate adapter must be
`async squeeze(ctx) -> dict`: starts holding over cup, actuates only squeezing,
finishes still holding, and raises on failure. It must not open, transport or
connect independently. Setting READY without implementing this contract is not
a substitute for integration. Offline pose validation remains available meanwhile.

## Evidence

Tests cover root-script selection, return to supported source rather than elevated
shake start, lower placement speed, grasp failure, loaded start, action/placement
failure, cancellation, wrong frame, missing/out-of-bounds poses, missing squeeze
adapter, and offline commands making no connection. No real arm commands were
issued during this work.
