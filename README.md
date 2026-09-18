# Viam pickup and pour trials

A small, supervised physical experiment loop: **teach → run → inspect → label →
change one parameter → repeat → save a lesson**. No simulator, ROS, custom IK,
or training server. This follows the skill-refinement idea in
[ASPIRE](https://github.com/NVlabs/ASPIRE), without importing its YAM-specific stack.
This is a trial harness, not autonomous reinforcement learning or a reproduced
ASPIRE benchmark. A coding agent/operator refines the saved skills from evidence.

## What Viam already does

| Need | Reused primitive |
| --- | --- |
| Tool pose and arm motion | `MotionClient.get_pose` / `move`, frame system, collision planning |
| Grasp/release/check holding | `Gripper.grab` / `open` / `is_holding_something` |
| RGB + depth evidence | `Camera.get_images`, raw bytes and capture metadata |
| Arm state and stop | `Arm.get_joint_positions`, `RobotClient.stop_all` |
| UFactory speed / optional G2 force | Documented module `do_command` extensions |

References: [Viam pick-and-place workshop](https://docs.viam.com/tutorials/pick-and-place/),
[motion API](https://python.viam.dev/autoapi/viam/services/motion/client/index.html),
[UFactory module](https://github.com/viam-modules/viam-ufactory-xarm).
The installed module and gripper must support any extensions used here. Standard
Viam Grab has no portable force argument; G1/Lite must not be treated as G2.

## Minimal station setup

1. Use a fixed marked source location, fixed receiving bowl, spill tray, and a
   scale to record total source mass (container + contents). Begin with an empty
   rigid cup, then known added masses, then soft cartons. Reset fill each trial.
2. In Viam, verify the arm model, payload, gripper TCP, wrist-camera transform,
   conservative acceleration/collision settings, and table/wall/container geometry.
   Include the held container's clearance in the configured tool collision envelope.
   Wrist RGB-D alone does not populate the planner's obstacles or estimate stiffness.
3. Install with `pip install -r requirements.txt`; set credentials in `.env` using
   `.env.example`. The existing `main.connect()` is reused.
4. Copy `station.example.json` to `station.json` if it does not exist. Resource names
   match the queried machine: `arm`, `gripper`, `cam`, and `builtin`.

```sh
python trial.py inspect
```

Inspect only reads. It saves inventory and camera samples under `runs/inspect-*`.
The camera must supply color and depth through GetImages. Configure this in Viam
if inspection shows only one stream. Point-cloud support alone does not prove both
streams work. Gripper holding feedback must work before execution.

Read-only check on 2026-09-18 verified 1280×720 RGB, raw depth, joint/tool state,
and holding feedback. The gripper torque query returned an empty object, so this
station's adjustable-force capability is **not confirmed**; native mode remains
selected. No arm/gripper motion was sent during setup. Teach the task poses before
running. The current gripper reported holding an object during inspection.

## Teach once per object geometry / station layout

Jog with Viam's controls to each pose, then capture it. Teaching does not move or
make the arm enter manual mode. Poses are **gripper TCP in world**, mm and Viam
orientation-vector degrees (not Euler angles). Do not copy simulation coordinates
or alter theta as though it were a wrist joint angle.

```sh
python trial.py teach pregrasp    # empty gripper, just above the source
python trial.py teach grasp      # fingers around source, object supported
python trial.py teach lift       # a small initial test lift, same orientation
python trial.py teach pour_ready # source upright above receiving container
python trial.py teach pour_tilt  # calibrated pour pose, accounting for lip position
```

Only the first three are needed for pickup trials. Set measured `workspace_mm`
bounds in station.json, review the poses, then set `calibrated` to true. Re-teaching
clears this flag. Use `--replace` when intentionally updating an existing pose.
All upright transport poses should share an orientation: moves request Viam linear
constraints with 5 mm position and 5 degree orientation tolerances. Pour transitions
allow rotation. If planning fails, inspect geometry/poses rather than bypassing it.

World geometry already configured on the machine is used by the motion service.
Optional `world_state` accepts Viam protobuf JSON for additional obstacles. The
workspace check validates waypoint endpoints; it is not a whole-arm safety cage.
Configure actual obstacles and verify physical clearance on the station.

## Run repeated trials

Edit `profiles/cup.json`: its 100 g value is an example, **replace with measured
total mass**. Label compliance `rigid`, `semi_rigid`, or `soft`. Copy profiles to
compare settings. Keep geometry fixed within a batch; teach a new config when the
container shape or placement changes.

```sh
python trial.py run --task pick                  # offline config validation; no connection
python trial.py run --task pick --execute        # one physical trial
python trial.py run --task pick --execute --repeat 5
python trial.py run --task pour --execute        # after reliable pickup
python trial.py label runs/TRIAL_ID success --poured-g 25 --notes 'no visible deformation'
python trial.py report
```

Start each trial at the taught pregrasp with an empty gripper and clear workspace.
The sequence is open → approach → grab → lift → hold/check → optionally pour →
return to source → release → retreat. Each repeat pauses for a human scene/fill
reset. A failure stops the batch; there is no automatic regrasp or recovery.
For soft objects, verify the configured gripping force before closing.

For a **verified UFactory G2** and compatible module, set `gripper_mode` to
`ufactory_g2` and `grip_force_percent` in the profile within the configured cap.
The runner checks the documented torque-read extension before writing force.
Percent is a controller setting, not calibrated newtons. With `native`, keep force
null: you can compare speed, poses, mass, and material, but cannot learn adjustable
grip force. The physical gripper/module version is still to be confirmed.

Label every outcome: `success`, `miss`, `slip`, `crush`, `spill`, `underpour`, or
`abort`. `completed` means the command sequence finished, never inferred success.
Use a receiving scale for poured grams; RGB-D/holding feedback cannot establish
pour volume or absence of deformation. Report groups exact profile + station +
task + runner code and includes failures, unlabeled totals, and evidence paths. It does not pool
different masses or silently choose/deploy a new policy. Record validated findings
using [lessons/README.md](lessons/README.md), then test at held-out masses/materials.

## Evidence and stop behavior

Each `runs/<trial>/` contains frozen profile/config/code JSON, `events.jsonl`,
`result.json`, and per-stage images, raw depth, camera metadata, tool pose, joint
state, and holding feedback. These are discrete observations, not a video or a
synchronized high-rate training dataset. Viam depth `.dep` is preserved losslessly;
the SDK's `ViamImage.bytes_to_depth_array()` decodes it. Runs and credentials are
gitignored; profiles and short reusable lessons are versioned.

There are no camera calls between tilt and return upright, so image latency does
not extend the requested pour dwell. Flow can still occur during both movements;
dwell is not closed-loop volume control. Viam motion may also incur planning latency.

Timeouts, failed moves/grabs, lost holding, observation errors, Ctrl-C, and SIGTERM
request StopAll and persist an aborted result. They never automatically open a
loaded gripper. Inspect and manually recover before restarting. `python trial.py stop`
also requests StopAll. Software stopping depends on connectivity; keep the physical
E-stop accessible. No physical task has been validated by this setup.

```sh
python -m unittest discover -s tests -v
```

Tests use a fake adapter to check failure/cancellation paths and evidence handling;
they are not a physics simulation. The older drink-menu files (`main.py`, `pour.py`,
`config.py`, `perception.py`) remain prototypes; use `trial.py` for these trials.
