---
name: taught-drink-demo
description: Run this Viam station's taught coconut-water and pitcher sequence using saved episodes, fresh camera comparisons, explicit grasp gates, and return home. Reuse the observed setup and recovery findings without redoing unrelated tests.
---

# Taught drink demo — v2

**Candidate — partial grasp/lift, stopped on pose mismatch.** Coconut pregrasp,
grasp-pose arrival and force-limited closure were observed once. The pitcher
visibly shifted during the approach, consistent with possible finger contact.
Lift stopped at waypoint 14. No pouring or pitcher pickup has been validated.

Use [the runtime guide](../../../../docs/teach_replay.md) for command syntax and
[task gating](../../../../agents/task_gating.md) for physical admission. A request
to run the robot authorizes the agreed sequence, not unobserved retries. For the
2026-09-19 live run: one admitted attempt per object, no automatic motion retries.
A connection failure before a motion RPC was sent is not a physical attempt.

## Revised composition

Candidate under live evaluation. Following the stopped v1 joint replay, the user
requested landmark-based execution using judgment. Use the existing motion planner
to reach demonstrated full tool poses for lift and transport. For pouring, preserve
ordered tilt landmarks and return upright in the same command, with no observation
pause while tilted. Gate placement/release and the next pickup on fresh cameras.
Do not widen tolerances; commanding poses avoids treating asynchronous pose/joint
readings as a simultaneous constraint. One revised attempt per object is admitted;
no automatic motion retries. Source: `runs/live_demo_20260919/review.md`.

## Start quickly

1. Inspect the newest **complete, operator-successful** episode for each object.
   Validate recorded waypoints and station compatibility before connecting. The
   useful taught episodes at this station are coconut
   `episode_20260919T150501_7cc56960` and pitcher
   `episode_20260919T151529_7b6554a0`; resolve their directories under
   `demonstrations/`, which is ignored by Git. Do not assume newer data is identical.
2. Read live tool pose, joints and holding state; capture both views. Compare the
   carton/pitcher, receiving cup, handle orientation **and neighboring clearance**
   to the episode images. A matching target alone does not admit the approach.
3. Keep one robot connection for gated execution where practical. Treat grasp,
   lift, pour, place/release, next-object entry and final home as observation gates.
   Use the existing full-pose planner for taught approach poses; the yaw-only
   `go_to_pose` changes the taught horizontal wrist orientation.
4. Reuse original pour waypoints and timing; avoid turning operator waits into
   extra time holding an open container tilted. Read-only checks and targeted
   validation are sufficient when no implementation changed; do not repeatedly
   run the entire test suite during an unchanged physical trial.

## Observed station-specific findings

- The current saved home is a full measured tool pose plus six joints in
  `config/local.json`. On this run the arm was empty-handed near that home.
  `demos/home.json` returns to it through the existing motion planner. Inspect
  current state before using it; returning home while holding is not this recipe.
- `arm.do_command({'exit_manual_mode': True})` returned
  `{'status': 'exited manual mode'}` on this machine. This establishes the normal
  control-mode transition without changing calibration or hardware limits. The
  pregrasp move then reached within **0.064 mm / 0.033 degrees** of the taught pose.
- Coconut image comparisons across three front-label patches agreed within
  **0–1 px horizontal and -1 to -2 px vertical**. The homography's approximate
  differential projection was **0.7–1.6 mm**, not a new validated localization model.
  Matching the wrist view at the same taught pregrasp pose was useful confirmation.
- The pitcher initially appeared **27–28 px farther toward the carton** than in
  its reference. Its body matched but its reflective handle did not match reliably.
  That narrowed the carton’s left-finger approach. The robot paused at pregrasp and
  the operator moved the pitcher away. Re-observe after any such intervention;
  don't assume a requested shift was achieved exactly.
- **The subsequent approach still displaced the pitcher.** Its location changed
  between `overhead_live_01_observe_0.jpg` and
  `overhead_live_02_grasp_pose_0.jpg`, while the carton remained in place. Grasp-pose
  arrival was about 0.20 mm from the taught target, yet clearance failed. The earlier
  request for 1–1.5 cm separation was insufficient as an admission rule. Next run,
  establish that the pitcher clears the full open-finger swept path before moving;
  visible space beside the carton at pregrasp alone is inadequate. If that cannot
  be established, have the operator temporarily relocate the pitcher, and freshly
  restore/localize it before its own task. This revised setup is unvalidated.
- Both episodes store operator-entered force: coconut **10%**, pitcher **20%**.
  Earlier teaching failed because torque readback returned no numeric torque.
  Record operator values as unverified; do not call torque readback again merely
  to repeat that discovery. Generic replay currently pauses for manual closure.
  The user subsequently requested automatic 10% closure in this live run.
- **Force command routing matters.** The standalone
  `gripper.do_command({'set_gripper_torque': 10})` returned `{}` without establishing
  a setting. Inspection of the current [official module source](https://github.com/viam-modules/viam-ufactory-xarm/blob/main/arm/gripper.go)
  found that its gripper handler drops standalone torque get/set commands but
  forwards `grab_with_torque`. That command applies force with the move through
  the arm handler. It accepts `position`, `speed`, and `torque`; the live candidate
  uses closed position 0, the existing speed obtained with `get_gripper_speed`,
  and torque 10 within the unchanged local force ceiling. Do not follow a dropped
  setter with ordinary `grab()`, which could use a different configured force.
  **Observed on this station:** `grab_with_torque` with `position=0`, `speed=2000`
  (read from the module), `torque=10` completed; holding changed false→true and
  both camera views showed the fingers closed around the coconut carton.
  Evidence: `coconut_force_10.json` and associated images. This is a commanded
  controller percentage, not a measured contact force in newtons. Lift/pour
  performance must be checked separately; do not infer pitcher success from it.
- Pitcher waypoint **198**, within `transport_back`, follows a **21.88-degree**
  joint jump and **67.6 mm** tool displacement over **0.03 s**. Standard replay and
  compaction must refuse that gap at the existing 20-degree limit. Do not increase
  the limit or fabricate intermediate samples. Re-teach that segment, or separately
  gate a motion-planned transition between the two recorded full poses and verify
  its result before continuing. That alternative is still unvalidated.
- **Coconut lift stopped at waypoint 14:** StopAll returned, and fresh observation
  showed holding true, maximum joint error **0.0382 degrees**, tool-position error
  **5.6464 mm**, and orientation error **0.6704 degrees** against that sample.
  Its sequential pose/joint read spans **292 ms** during hand-guiding. Time skew
  is a plausible explanation, not a proven hardware fault or permission to widen
  tolerances. Preserve the raw sample and stopped state; do not replay the same
  lift blindly. A future recording should capture mutually consistent state or
  flag read skew before replay. This run elected a planned return to the measured
  starting grasp pose while retaining the object, followed by support verification.

## Connection and recording recovery

A WebRTC connection timed out **before** the coconut grasp motion was sent; the
last verified state remained pregrasp. Establish fresh state after reconnecting.
Never resend a motion whose completion is uncertain.

Teaching has a separate read-only reconnect wrapper and `--resume latest` support.
The installed Viam SDK's background reconnect code calls `sys.exit()` after retry
exhaustion, so teaching disables that loop and owns reconnection. Save samples and
frames continuously, preserve completed phase checkpoints, and repeat an incomplete
phase rather than splice unknown motion into a replay track. Keep partial attempts
as evidence. A failed StopAll while disconnected does not prove the arm stopped.

`VIAM_DISABLE_WEBRTC=1` selects direct transport as a diagnostic option; do not
call it a proven network fix. It failed to connect in this run; ordinary WebRTC
subsequently connected and completed the grasp-pose command. Reuse a working
connection for phase gates instead of routinely reconnecting between commands.

## Evidence and status updates

Private raw evidence lives in `runs/live_demo_20260919/`: `review.md`,
`events.jsonl`, `observation.json`, `coconut_pregrasp.json`, `image_comparison.json`,
`coconut_grasp.log`, and the associated camera files. These are local artifacts,
not available to a fresh checkout unless the operator supplies them. The evidence
summary above is the reusable record; reassess station/camera changes.

Before admitting each next phase, append what actually happened and its observation
source to the run review. Update this skill's status and findings with observed
successes/failures. Keep any unexecuted transition, unverified force, missing liquid
outcome or unknown held state explicit. One successful approach does not validate
an entire drink skill.
