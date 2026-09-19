# Teach and replay

Run from the repository root with the installed `.venv`. This is a supervised
candidate workflow: automated tests are not physical validation. Teaching is
read-only apart from StopAll on failure. You control manual/free-drive mode and
the gripper with your existing Viam/UFactory controls. No machine configuration
is changed by these commands.

```sh
.venv/bin/python -m runtime --config config/local.json teach coconut_water
.venv/bin/python -m runtime --config config/local.json teach pitcher --grasp-type handle

# Continue the most recent interrupted coconut episode in the same directory:
.venv/bin/python -m runtime --config config/local.json teach coconut_water --resume latest

# Offline dataset validation, no connection or movement:
.venv/bin/python -m runtime --config config/local.json replay-demo

# Physical replay, with live operator gates and offset entry:
.venv/bin/python -m runtime --config config/local.json replay-demo --execute

# Return only to the existing taught home pose (free-drive OFF, path clear):
.venv/bin/python -m runtime --config config/local.json run demos/home.json --execute
```

Your local task config must contain measured workspace bounds, `calibrated: true`,
`coconut_water` and `pitcher` in `objects`, and `wrist`/`overhead` in
`primitive_settings.camera.views`. Both object names are in the example task
config; its uncalibrated bounds deliberately cannot execute. The local station
already has both camera views. Credentials continue to come from `.env`.

## Teaching one episode

1. Place the object in its expected region. Keep the arm stationary while capturing
   the before images and pose. Confirm the hand is empty.
2. Hand-guide to pre-grasp and press Enter. Hand-guide to grasp and press Enter.
   These save the full tool pose in the configured frame, joints when supported,
   and wrist/overhead frames at grasp. Pre-grasp is the clear approach pose before
   the final reach to the object; home can serve this purpose if its approach is
   safe. It is not automatically home, and the recorder never moves there for you.
3. At the gripper gate, close the gripper manually and adjust the force through
   your existing controls until the grasp is secure without damaging the object.
   Teaching waits for you; type `yes` only when the force is right and the grasp
   succeeded. Then enter the force percentage you set. It saves
   `gripper_force_percent` plus timestamped `gripper_force_evidence` immediately,
   explicitly marked `operator_entered` and `hardware_verified: false`.
   Invalid or out-of-limit input prompts again. No torque readback or gripper
   command is made during teaching. This is a setting percentage, not measured
   contact force in newtons. Both
   cameras are captured again, including every wrist depth stream provided.
4. Record each named phase: **lift, transport, pour, return_upright,
   transport_back, place, retract**. Press Enter to begin that phase, hand-guide
   through it, and press Enter to finish. Sampling defaults to 5 Hz (`--hz 1..20`)
   and can be slower if pose/joint RPCs are slow. Every sample has acquisition
   start/end timestamps, so the actual cadence is inspectable.
   Both cameras also capture at **2 Hz** during each phase, including wrist depth
   when available. Set `--camera-hz 1` for fewer images or `--camera-hz 5` for more.
   The camera loop runs separately from joint sampling; slow cameras lower the
   actual image rate without building a backlog. Images go under `frames/<phase>/`,
   with timestamps, poses and episode-relative paths appended to `frames.jsonl`.
   Checkpoint pictures are still captured. Connection failures trigger read-only
   reconnect/retry; invalid data and other non-network errors pause the episode.
5. At the end of place, support the object, open the gripper manually, and confirm
   release. Capture after-place context, then record retract. Confirm the whole
   manipulation succeeded. Type `q`, refuse a confirmation, or press Ctrl-C to abort.

Hold still at phase boundaries, including photographs. The cameras and pose reads
are sequential, not hardware-synchronized. Each camera retains its reported
capture timestamp; each robot sample records its read interval. Gripper closure
and release are explicit events, not inferred from joints. Free-drive stays under
your control; turn it OFF before replay. Teaching has no total phase deadline, so
reconnecting or waiting for the operator cannot expire the recording. Each read
attempt is limited to 15 seconds. Teaching never automatically closes or opens
the gripper. Replay retains its motion and total-run deadlines.

Every run creates a new directory; failed episodes are retained and excluded from
replay. Current selection is the newest complete, operator-successful episode per
object. An invalid newest successful episode fails validation instead of silently
falling back to an older one. Python callers can select an exact `episode_id` with
`load_episode`.

## Connection recovery and interrupted processes

Teaching manages its own connection instead of the SDK background reconnect loop
(the installed SDK can exit the process after exhausting that loop). A failed
camera/pose/joint read reconnects and retries until successful or cancelled. Wait
with the arm still when `connection_lost_hold_arm_still` appears. Retry attempts
and gaps are logged. Teaching reconnects never retry arm motion or gripper commands.

Press Ctrl-C to pause; `q` also cancels at an active prompt. If you already pressed
Enter to finish a phase while disconnected, Ctrl-C still cancels the pending read.
An interrupted process can be resumed with `--resume latest` or
`--resume episode_<id>`. The same episode directory is used; completed phases,
grasp poses, force and checkpoint images are retained. The unfinished phase is
recorded again from its start, after you manually restore the arm, object, gripper
and (for pouring) contents to that phase's starting state. No automatic reset occurs.
An episode lock prevents two new recorder processes from writing it concurrently.

Every sample/image record is appended as it arrives. Phase completion commits a
trajectory and cursor to disk. On resume, prior metadata/trajectory are archived
under `interruptions/`; unfinished samples and images remain in the raw logs and
their uniquely named attempt directories. Old failed episodes without a cursor
are supported by conservatively repeating their last logged phase. Even after a
forced process kill, completed phase checkpoints remain reusable.

If a connection drops during a trajectory phase, recording resumes after reconnect
and keeps the captured data, but completing that phase prompts you to repeat only
that phase. Missing arm motion cannot be reconstructed. The trajectory selected
for replay excludes the incomplete attempt; all raw attempts remain inspectable.
Other phases are retained. StopAll is attempted on cancellation/non-network failure;
if the connection is down, its failure is logged and you must verify state manually.

```text
demonstrations/coconut_water/episode_<UTC>_<unique-id>/
  metadata.json
  trajectory.json
  samples.jsonl
  events.jsonl
  overhead_before_0.jpg
  wrist_before_0.jpg
  wrist_before_1.dep
  ... grasp / after_grasp / after_place, for both views ...
  frames/<phase>/<attempt>/...
  interruptions/<resume-id>/...
```

File extensions match the camera's original encoding; stream indices/names and
MIME types live in `metadata.json`, so depth need not be guessed from filenames.
Raw Viam depth is retained without lossy conversion. Missing depth is an absent
stream, never fabricated. Camera images include hashes, dimensions and timestamps.
`metadata.json` contains object/episode identity, full pre-grasp/grasp/place poses,
joints, grasp type, notes, explicit grasp/overall outcomes, observations and station
provenance. A successful teaching episode remains `validation_status: candidate`.

`trajectory.json` uses the existing **joint-track/1** envelope: `arm`, `units:
degrees`, `joint_count`, `waypoint_count`, `duration_s`, `stats`, and
`waypoints: [{t, joints, ...}]`. Additive fields are the full tool pose, phase and
read timestamps. Times start at zero. No smoothing or joint-value rounding is
applied. If joint reads explicitly report `NotImplementedError`, the episode uses
`pose-track/1` and replay uses the existing full-pose planner throughout. Other
joint read failures abort, so a lost connection cannot masquerade as optional data.
The older experimental replay does not understand episode gripper/phase gates;
use `runtime replay-demo` for episodes. Existing experimental tracks are untouched
and are not imported automatically: they lack grasp/context/phase evidence.

## Replay and limits

Replay validates both datasets first. For each object, it asks you to confirm a
clear swept path, empty hand, fixed cup, same upright object orientation, available
return spot, and free-drive OFF. It captures a fresh scene and prints the reference
and current image directories. Enter a **measured** `dx,dy,dz` in millimetres in the
configured task frame and explicitly confirm it is reliable. Zero is also an
explicit measured claim. Abort if uncertain: there is no detector or silent zero
fallback. The existing overhead homography reports 27 mm mean / 68 mm maximum error
and does not measure height, so raw overhead pixels alone cannot support a precise
grasp offset. For operator-entered force episodes, replay pauses at the grasp pose
and displays the saved force; set it and close manually, then confirm. No torque
readback is attempted for these episodes. Holding-state checks still apply during
physical replay. Older episodes with hardware force evidence retain the verified
automatic-close path. Older episodes without a saved force prompt for the
team-approved force and use that automatic path; reteach to use manual closure.

If Rust/WebRTC `channel closed` messages persist, try the optional direct gRPC
transport (a diagnostic alternative, not a verified fix for your network):

```sh
VIAM_DISABLE_WEBRTC=1 .venv/bin/python -m runtime --config config/local.json teach coconut_water
```

The failed episode at `episode_20260919T142938_9354fa60` completed grasp pose/image
capture despite those messages, then aborted specifically on missing torque
readback. Its StopAll call returned successfully. It can now be resumed after
restoring the pending step's starting state; existing data is preserved.

Only pre-grasp, grasp, and the lift phase are translated. Every orientation is kept.
The remaining joint waypoints, including pour, transport back, place and retract,
are replayed unchanged, in their original order. The object returns to the
**original taught place**, which must be clear; this version does not adapt the
return placement. Intra-phase timing is paced at the recorded rate or slower if
RPC/motion completion takes longer. Operator wait time between phases is omitted.
This preserves commanded joint targets, not exact motor velocities or interpolation.

Full taught poses use the existing `motion.plan_to` helper, because the public
`go_to_pose` tool forces a downward orientation. Recorded joint moves use the
existing Viam Arm API pattern and **do not run through collision planning**.
Workspace checks validate endpoints, not all intermediate links or swept volume.
The operator must inspect clearance, including the connection from the translated
lift back to the original transport. A live joint-step guard rejects a discontinuous
IK transition; it does not establish collision clearance.

Defaults:

- Maximum XY offset magnitude: 20 mm; maximum absolute Z offset: 10 mm.
  `--max-xy-mm` and `--max-z-mm` configure these, within 50/20 mm outer limits.
- Maximum per-joint step, in the recording and from live joints: 20 degrees.
  `--max-step-deg` can tighten it. Existing machine limits and motion tolerances apply.
- `--pause-s 0.2` pauses at phase boundaries and between approach/grasp.
- Scene evidence must be no older than 300 seconds at pickup admission.
- The task's primitive and total-run timeouts apply. One attempt, zero automatic
  recovery attempts. Any failure/cancellation requests StopAll and never auto-opens
  a possibly loaded gripper. A StopAll failure is logged explicitly.

Replay checks live holding state, verifies pose/joint arrival, asks for observed
grasp success, gates release on confirmed support, and gates the next object on
confirmed pour/return/empty-hand outcomes. Pitcher localization is refreshed only
after coconut completes. Unknown/failed gates halt. Re-establish state before a new
physical attempt. Logs, adapted plans, live captures and results go under
`runs/teach_replay/`; raw demonstrations and runs are Git-ignored.

## Compacting a taught track

Teaching samples at a fixed rate whether or not the arm is moving, so most waypoints
carry no shape: in the recorded coconut episode the median consecutive joint change is
0.003 degrees and over half of the samples sit inside a pause. Every joint waypoint
costs replay two state reads and a move, so the dead ones are the bulk of both the file
and the replay time.

```sh
.venv/bin/python -m runtime --config config/local.json compact
.venv/bin/python -m runtime --config config/local.json replay-demo --compact
```

`compact` reads the same episode replay would select and writes
`trajectory_compact.json` beside the immutable `trajectory.json`. It is offline: no
connection, no motion, and the taught track, images and metadata are never rewritten.
Replay keeps using the full track unless you pass `--compact`.

The compact track is a **subsequence** of the taught waypoints, retimed. No joint
vector or pose is ever invented, so the tool-pose check against the taught pose keeps
its meaning and no fabricated configuration is commanded. A waypoint is dropped only
when the straight joint-space chord between the neighbours that survive stays within
`--tol-deg` (default 1 degree) of it, and a span is split whenever its chord would
exceed `--budget-deg` (default 12), so dropping waypoints cannot manufacture a step
near the 20 degree replay limit. Time between kept waypoints keeps every second the
arm was moving and caps the seconds it stood still at `--max-dwell-s` (default 0.25),
measured on the original samples so a collapsed hesitation never shortens the motion
around it. `pour` is exempt by default (`--keep-dwell`): holding a tilt is how the
liquid leaves the bottle, not hesitation. Operator wait between phases is dropped
because replay re-bases its clock at each phase and never paced it anyway.

Observed on the taught episodes: coconut 350 to 66 waypoints and 251 s to 59 s of
paced motion, 230 KiB to 50 KiB; pitcher 264 to 84. `--tol-deg 0.5` keeps more shape,
`--tol-deg 2` fewer waypoints. The output is verified before it is written: subsequence,
phase order, monotonic time, the taught release waypoint, and the step limit. Dropping
waypoints does let the arm cut corners between the kept ones, within the tolerance;
the swept path is still yours to inspect, and `--max-step-deg` and the live joint-step
guard are unchanged.

## Sparse samples are not dense teaching

`Recorded joint step exceeds replay limit; reteach more densely` can mean the opposite
of what it says. In the taught pitcher episode one pair in `transport_back` is 21.88
degrees apart with a recorded `dt` of 0.029 s, which reads as a violent jerk. It is
not: that pair spans a joint read that took 2.07 seconds, and `t` is stamped when a
read completes, so the elapsed time between the two latched configurations is the
2.10 s read window, an ordinary 10.4 degrees per second of hand guiding. Compare
`timestamp` and `completed_at` on the two waypoints before concluding anything about
speed.

Compaction cannot repair this. Two adjacent taught waypoints cannot be subdivided by
any subsequence, and the arm motion between them was never sampled; interpolating one
would invent both a joint vector and a pose the arm never reported. `compact` reports
such a pair and refuses to write that episode. The fixes are to re-record the
demonstration moving slower where reads thin out, and to reduce read latency:
`--hz` cannot outrun the RPC round trip, which on this station has a median of 0.13 s
(coconut) to 0.22 s (pitcher) and a tail past 2 s. `VIAM_DISABLE_WEBRTC=1` is worth
measuring against that tail.

Also worth reading before trusting an episode: `return_upright` in the recorded
coconut demonstration holds still for 11 seconds and travels 0.02 degrees total. The
phase is present, so validation passes, but nothing is taught in it.

## Scene-paired reference traces

`compact` also writes `reference_trace.json`: the compacted waypoints joined to the
camera frames captured beside them, plus the phase structure, the taught grasp, and a
written list of what the episode does not establish. It is advisory. Replay runs
`trajectory_compact.json`; nothing executes the reference trace.

Pairing is on **absolute UTC**, per phase and attempt. The frame log's `t` and the
trajectory's `t` do not share an origin (in the taught coconut episode they differ by
about 5.8 s), so joining on `t` silently mismatches scenes by seconds. Each pairing
carries a signed `offset_s` and the `pose_error_mm` / `joint_error_deg` between the
waypoint and the arm state recorded with that picture, so a reader can tell whether an
image shows the waypoint or merely its neighbourhood. A waypoint with no frame inside
`--pair-age-s` gets `scene: null` rather than a nearby stand-in. Observed: 62 of 66
coconut waypoints paired, most within a quarter second and under a millimetre.

Every waypoint carries both views and the raw wrist depth, so the trace is also the
index a localizer would train and evaluate against, without reopening the raw episode.

## Planner-driven tracks

```sh
.venv/bin/python -m runtime --config config/local.json compact --emit pose-track/1
```

The same subsequence, declared for the pose path. Replay then drives each taught tool
pose through `motion.plan_to` instead of commanding the taught joint vector, and the
motion service plans continuous, collision-checked motion between waypoints. This is
the only smoothing on offer: filtering the recorded joint values would invent
configurations the arm never reported, and replay's check of the reached tool pose
against the taught pose would then be comparing against a pose no one measured.
Smoothness comes from handing the planner fewer, cleaner waypoints.

The trade is real and runs both ways. The pose path adds collision planning that the
joint path does not have, and the 20 degree joint step limit does not apply to it, so
an episode blocked as a joint track can validate as a pose track. It also lets the
planner reach a taught pose in a different arm configuration than the one taught, and
it plans its own route across a gap that was never sampled. `compact` warns and names
that span instead of blocking. Inspect it, and the swept path, before `--execute`.

## Localization upgrade without changing episodes

`estimate_object_offset(reference_frame, current_frame)` in `runtime/replay.py` is
the deliberately unimplemented localization boundary. `replay_skill(skill_name,
current_scene, ...)` accepts an offset plus object/frame identity, timestamps,
reliability and source evidence. `run_demo` currently supplies this through
supervised measurement.

A future Grounding DINO, YOLO or Astra-based localizer can identify the same object
in saved and current RGB frames, use wrist depth plus camera intrinsics/extrinsics
to obtain metric positions, transform both into the saved task frame, then return
their translation difference. Saved stream dimensions, raw depth, tool poses,
object labels, success reports and calibration snapshots remain the same training
and evaluation data. Add detections/confidence/uncertainty as derived sidecars;
keep raw episodes immutable. The adapter must account for image resizing, robot
motion and camera synchronization, reject orientation changes and uncertain
matches, and still obey offset/workspace gates. Camera intrinsics and rigid camera
extrinsics must be versioned alongside the station calibration for metric depth
projection; a planar homography alone is insufficient. No ML dependencies or
unvalidated detector have been added.

Implementation is split between `runtime/teaching.py` (capture),
`runtime/replay.py` (adaptation/execution), and `runtime/demonstrations.py`
(storage and thin Viam adapter). Existing human-owned primitive internals and
planner registry contracts are unchanged.
