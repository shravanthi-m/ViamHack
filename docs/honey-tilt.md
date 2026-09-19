# Honey: tilt only (candidate, not physically validated)

This replaces the requested honey **behavior**, without modifying the teammate's
physical squeeze primitive. `runtime.honey_tilt` composes existing motion, gripper,
camera and localization primitives. There is exactly one normal grasp closure;
no squeeze command, pulse or repeated grab. The existing pour entry points are
placeholders, so this uses taught full poses rather than pretending they work.

Sequence: detect honey and cup together → approach bottle → grasp → close/verify
holding → lift → cup approach → upright over cup → taught tilt → short dwell →
upright → cup approach → source lift → supported original grasp position → settle
→ release/verify → retreat. The source and cup routes are translated independently
in XY using their detected targets, within an operator-reviewed displacement limit.
Bottle orientation, height, grasp geometry and cap/nozzle state must not change.
Tilt-only may not dispense viscous honey; completion does not measure flow/amount.

## Record

Use the same environment and read-only teacher as `taught-shake-squeeze.md`.

```sh
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json init honey
```

Move manually and exit manual mode before **each** capture. Run one line only
after positioning for that label:

```sh
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach inspection_pose
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach source_approach
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach source_grasp
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach source_lift
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach cup_approach
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach cup_action
python -m runtime.taught_actions --config config/local.json --book runs/honey-poses.json teach cup_tilt
```

- `inspection_pose`: camera posture from which both bottle and cup are visible and
  **at which the planar localization calibration was measured**. Required even for
  fixed-camera use to keep the candidate's start posture constrained.
- `source_approach`: gripper clear of the bottle, aligned for entry.
- `source_grasp`: fingers ready to close around the bottle while it is supported.
  This is the final placement pose too.
- `source_lift`: bottle-clearance waypoint above that supported pose.
- `cup_approach`: clear waypoint near the receiving cup, with space for the bottle.
- `cup_action`: upright bottle positioned for dispensing over the cup.
- `cup_tilt`: measured full tool pose placing the tilted bottle's outlet over the
  cup, without colliding. Teach translation AND orientation as needed: rotating
  around the gripper is not rotating around the bottle outlet. Do not merely edit
  `theta`, which is not a general-purpose pour-angle control.

Teach with known bottle-to-gripper geometry and appropriate human support;
recording commands do not move or grip. Never depend on manual mode to support an
unsecured bottle. For initial trials use an empty bottle/empty cup.

Fill the common book settings described in the shake guide, then:

- `source_object` / `cup_object`: actual configured object IDs (defaults honey/cup).
- `tilt_dwell_s`: reviewed dwell, 0–5 seconds. Not a measured pour amount.
- `max_translation_mm`: physically reviewed XY shift envelope, 0–50 mm. This is a
  conservative software ceiling, NOT evidence that 50 mm is safe on this station.
- `inspection_calibration_sha256`: SHA256 of the localization calibration artifact
  measured and validated at the recorded inspection posture. Do not copy a hash
  from an unrelated camera calibration just to pass the check.

## Localization prerequisites

The existing localizer is a **planar image-to-task-frame map**, not generic wrist
RGB-D localization. Camera intrinsics alone are insufficient. A moving wrist's
old planar map is invalid at a new posture. The runner checks tool pose within
1 mm/0.5° AND all recorded joint positions within 0.5° before and after capture/
inference. This is a guard, not calibration accuracy evidence.

Both configured objects need measured `primitive_settings.localization.target_profiles`
for the same view/model/calibration, using the existing `localization-profile/1`
schema. Missing profiles block execution before connecting. The honey profile
must map detection to the **gripper grasp target**, and the cup profile to the
**upright over-cup gripper target**, not the bottle/cup surface or centroid. Their
height and full orientation must match the corresponding taught poses. Measured
error must meet the actual grasp/dispense tolerance throughout the permitted
region. Existing overhead artifacts do not establish that the wrist is calibrated.

One fresh RGB frame and one model request locate both objects. No repeated
inference is performed while moving. Missing/ambiguous detections, stale frames,
calibration/model mismatch, excessive shifts or out-of-bounds targets block pickup.
No arbitrary overhead fallback or fabricated depth transform is used. Keep the
scene stationary after capture. Depth is not used by this candidate.

```sh
# Offline, including measured-profile admission; no connection/inference/motion:
python -m runtime.honey_tilt --config config/local.json --book runs/honey-poses.json

# Only after physical readiness review; one trial, operator gate, no retries:
python -m runtime.honey_tilt --config config/local.json --book runs/honey-poses.json --execute
```

Restore the inspection posture manually first; detection never drives the arm
there. The generic taught-actions runner cannot execute honey without detection.
Faults during manipulation request StopAll and do not automatically release the
bottle. Never retry without observing the actual arm/bottle state. Endpoint
validation and slow placement are not contact sensing or proof of collision-free
paths; account for the held bottle and all swept paths in the station review.

Offline tests establish ordering, return location, single-inference use, fail-closed
admission and stop/no-release behavior. They do not establish physical success.
Credentials, station settings, images and recorded poses remain outside Git.
