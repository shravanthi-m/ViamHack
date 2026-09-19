---
name: pick-pitcher-handle
description: Plan and gate a horizontal grasp through the handle of the station's stainless steel pitcher, adapting to freshly measured pitcher position and handle direction. Candidate procedure; physical execution requires the missing motion and perception capabilities described here. Use for the handled metal pitcher, not the paper cup or an arbitrary vessel.
---

# Pick the stainless steel pitcher through its handle

**Revision v1 — candidate; physical grasp execution blocked.** No grasp trials have
been performed. This is a reusable instruction skill and an implementation
contract, not an enabled pickup primitive or a learned successful grasp.

The user requires the gripper to approach the handle horizontally. Success means
grasping the handle with the intended finger passing through its opening, lifting
the pitcher clear of its support, and retaining it upright without slipping or
unintended contact. A gripper holding report alone does not establish this.

## Start here

Work from the repository root. Read `agents/orchestrator.md`,
`agents/task_gating.md`, and the active config. For learning, also follow
`agents/fix_loop.md`, `agents/task_worker.md`, and `agents/skill_learning.md`.
Use `python -m runtime --config config/local.json tools` to inspect the current
public interface; reevaluate the gaps below if implementations have changed.

The only executable phase supplied in v1 is [01_observe.json](01_observe.json):

```sh
python -m runtime --config config/local.json validate skill_library/entries/pick-pitcher-handle/v1/01_observe.json
python -m runtime --config config/local.json run skill_library/entries/pick-pitcher-handle/v1/01_observe.json
```

The second command is a mock. For a requested live observation, append `--execute`
to capture overhead and wrist images without motion. Inspect the returned files;
capture completion proves only that images exist. The wrist may not currently see
the target. Its depth stream is useful only with verified calibration and valid
depth on the target; reflective metal may leave uncertain measurements.

## Current blockers

| Required evidence or capability | What is currently available | Admission consequence |
| --- | --- | --- |
| Horizontal tool orientation and constrained horizontal insertion | `go_to_pose` accepts x/y/z/yaw and forces tool-down; `go_to_origin` returns to one fixed taught pose | Cannot compose a horizontal handle insertion from the public tools |
| Fresh 3D handle pose, identity, opening geometry, and bounded error | `localize` is unimplemented; overhead mapping is planar, with reported mean error 27.01 mm and maximum error 67.85 mm | No contact target or handle height established |
| Measured handle-to-gripper alignment and clearance | No taught grasp, finger dimensions, insertion depth, or jaw alignment supplied; user confirmed no taught side grasp | Cannot choose orientation, contact pose, or insertion distance |
| Safe load and grip setting | `close_gripper` verifies torque setting and holding; no pitcher-specific force/load trial | Force ceiling is a limit, not a successful force recipe |
| Current empty/held state and post-lift retention | Camera captures and close/open feedback exist; public catalog has no standalone holding observation | Establish entry state without releasing anything; require fresh retention evidence after lift |

`motion.plan_to` is an internal helper for full poses, not a registered planner
tool. Do not bypass the registry with a direct SDK call or repurpose the configured
origin as a movable target. Do not use the spoon `pick_up` contract for a pitcher,
or use `shake` to obtain a horizontal orientation. Missing capabilities and their
acceptance requirements are specified in [requirements.md](requirements.md).

## Position-independent grasp construction

Use a measured **handle frame H**, not remembered image pixels or table coordinates:

- Origin: the selected grasp contact midpoint on the handle's outer segment.
- +X: horizontally outward from the pitcher body toward the handle.
- +Z: upward for an upright pitcher; +Y completes the right-handed frame.
- `T_world_handle`: a fresh calibrated transform with timestamp, object identity,
  frame, units, and position/orientation uncertainty.
- `T_handle_gripper`: a taught, verified transform for this handle geometry and
  these fingers. It includes the real tool reference offset and roll/jaw alignment.

Compute `T_world_gripper = T_world_handle * T_handle_gripper`. Rotate both the
translation offset and the orientation when the handle heading changes. Do not
copy the old tool yaw or translate an old world pose without rotating its offset.
For this upright-pitcher candidate, the intended inward approach direction is
`a = -X_handle` expressed in the world frame. The taught grasp must confirm that
this exterior approach passes a finger through the opening and contacts the
selected handle segment; otherwise reject this geometry rather than improvising.

For measured standoff `d`, grasp position `p`, and validated lift `h`:

- Pregrasp position: `p - d*a`, with the taught horizontal orientation.
- Insertion: advance along `a` at constant handle height and fixed orientation.
- Lift: `p + h*Z_world`, retaining the grasp orientation and pitcher upright.

These are geometric relationships, **not executable coordinates**. Do not fill in
`d`, `h`, grasp height, roll, finger aperture, force, or clearance from visual
guesswork. A horizontal endpoint alone does not constrain the intervening path.
Require an implementation that maintains the admitted insertion path and attitude.

Freshly localize on every placement and after any scene change. A table-plane
homography applied directly to the elevated handle does not give its 3D pose.
The tray also changes support height. Use verified 3D sensing or measured geometry
and a calibrated pose estimator; resolve occlusion and pose ambiguity before contact.

## Gated physical procedure once prerequisites are implemented

Compile separate runtime plans using the then-current catalog, with an agent gate
between phases. Never generate unknown tool names as if they were implemented.

| Phase | Entry evidence | Action and required exit evidence |
| --- | --- | --- |
| Observe | Live observation requested | Capture both views; identify this pitcher, support, handle heading, neighboring objects, and current robot/held state |
| Admit | Requirements in requirements.md satisfied; current run scope and finite budget recorded | Confirm clearances exceed the complete pose/tracking error allowance, target is reachable, load/force are within validated scope, and the entire robot/pitcher route is clear |
| Pregrasp | Verified empty gripper and clear opening path | Open with the existing primitive, then move to exterior pregrasp using the future supported horizontal motion capability; verify reached pose and view of handle/fingers |
| Insert | Fresh alignment evidence and clear aperture | Translate horizontally through the measured insertion distance, retaining tool attitude; verify intended finger placement and no pitcher displacement before closing |
| Grasp | Fingers surround the intended handle segment | Close at the validated force using `close_gripper`; require force readback, holding feedback, and visual confirmation of handle contact |
| Test lift | Confirmed handle grasp and clear lift envelope | Lift through the validated small height; observe separation from the support, upright retention, no slip, and current holding feedback through a supported observation source |
| Finish | Test-lift gate passed | For pickup, finish holding at the verified pose. During learning, reset only through a separately admitted supported placement/release phase or a verified operator handoff |

Include the pitcher, handle, tray, and nearby objects in collision reasoning; the
machine's static geometry alone does not establish their clearance. Do not home
or open as an unconditional first action when current state is unknown.

On failed/unknown gate: follow `agents/fault_recovery.md`; inspect results and fresh
observations. Never blindly repeat insertion, increase force, open a suspended
grasp, or retract from unknown contact. If supported recovery cannot establish a
safe state, request a specific operator reset. Preserve all failed attempts.

## Learning and scope of generalization

Use [requirements.md](requirements.md) for the proposed finite trial matrix,
measurements to freeze, and promotion criteria. Change one exposed parameter per
candidate; preserve earlier candidates and record actual outcomes. Mock success
does not count as grasp evidence.

“Different locations” means observable, reachable poses with a verified clear
approach, the same measured handle/fingers, and supported load conditions. This
skill cannot promise pickup anywhere on the table or with any cup. Placement,
handle heading, support height, fill, and camera changes can invalidate admission.

## Evidence and handoff

- Prior live overhead observation, 2026-09-19 02:47:28 UTC: a steel handled pitcher
  on a green tray, with its handle on image-right. This established appearance,
  not height, grasp clearance, fill, or a motion target. It is stale for future runs.
- Pre-trial observation, 2026-09-19 02:56:25 UTC: the overhead camera again showed
  the pitcher upright on the green tray with the handle on image-right. The wrist
  color view showed the floor and table edge, not the pitcher, handle, fingers, or
  grasp area. Trial 1 was therefore blocked before motion: the current camera
  arrangement cannot verify intended finger placement, and the motion/localization
  gaps above remain. The user authorized up to 15 trials; zero were consumed.
- Source inspection: `primitives/motion.py`, `primitives/registry.py`,
  `primitives/localization.py`, `primitives/gripper.py`, and
  `config/overhead_homography.json` establish the capability gaps above.
- Local raw evidence: `runs/overhead_observation_20260919/20260919T024726-e1fb6cc4/`
  and `runs/fix_loop/pick-pitcher-handle/`. These are ignored and may be unavailable
  on another machine; reacquire evidence instead of assuming it exists.
- Physical grasp successes/failures: **0/0; not attempted because admission is
  blocked**. No prior validated revision. Observation plan validated, mocked, and
  executed live twice. The latest wrist view was unsuitable for grasp evaluation.

Suggested next-agent request: “Read
`skill_library/entries/pick-pitcher-handle/v1/SKILL.md` and pick up the stainless
steel pitcher through its handle with a horizontal approach. Recheck its recorded
capability gaps and current scene; execute only phases whose gates pass.”
