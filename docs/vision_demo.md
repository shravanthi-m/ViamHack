# Minimal vision-guided signature pour

The demo can estimate a small XY change in each source's pickup position from a
fresh overhead photograph. It matches two separated texture patches on the same
object against its taught reference, then converts their agreed pixel translation
using a measured local 2×2 map. It adjusts the existing pregrasp, grasp and lift
poses. The cup, pour, upright return and original source return positions stay
fixed. Automatic closure uses the existing per-object force settings.

This is a supervised candidate for the same props, fixed camera mounting, fixed
object orientation and height, and at most 20 mm source translation. It does not
estimate rotation/depth, detect every obstacle, or accept arbitrary object poses.
The operator still confirms the setup, adjusted approach clearance, intended grasp,
release support and actual pour outcome. It requires no new dependency or API call.

## One-time measured setup

Do this with the robot stopped; move props by hand only in a known safe workspace.
No calibration command below connects to or moves the robot.

1. Restore each source to its original taught position, orientation and supporting
   surface. Preserve the overhead camera mount/resolution. The reference is the
   episode's `overhead_before_0.jpg`; do not replace teaching images. If that setup
   cannot be restored, re-teach a consistent reference instead of assuming zero.
2. Choose two separated, high-contrast patches on the object. The local preparation
   folder `runs/vision_demo_setup/` includes candidate patch overlays and measurement
   JSON for the current carton/pitcher. These patches passed a same-image check
   only. The pitcher's reflections may make them unsuitable in changed lighting;
   measurement checks must decide that. Patches must not include another prop.
3. For each object, capture two fresh overhead photos after independently measured
   XY shifts in two nonparallel directions, relative to the taught starting place.
   Measure in the **robot task-frame axes and millimetres**, not image axes. Enter
   those displacements in `fit`. Use a ruler/jig aligned to established task axes;
   do not take measurements from the old inaccurate homography or model boxes.
4. Capture four additional measured placements around the taught starting place
   (for example, the corners of the small region you want to use). Enter these
   independent photos and displacements in `check`. Do not reuse fit photos.
   The runtime refuses offsets outside the polygon of these checked placements.
   Capture a missing-object photo and an occluded-object photo for `reject`.
5. Enter the primitive owner's approved `tolerance_mm`. All null values in the
   template are deliberately incomplete. Measurements include detector error;
   fitting residual alone is insufficient. The maximum error over fit **and
   independent checks** must be within the stated tolerance, and both negative
   photos must fail image matching. Use consistent scene lighting and fill levels.

Snapshot files can come from the observer's overhead capture, with the robot
stationary. Each object's measurement JSON uses this shape (paths are relative to
that JSON; fill real values rather than copying illustrative coordinates):

```json
{
  "measurement_source": "operator_measured_task_frame_mm",
  "patches": [[null, null, null, null], [null, null, null, null]],
  "search_px": 48,
  "tolerance_mm": null,
  "max_offset_mm": 20,
  "fit": [
    {"image": "fit_x.jpg", "offset_mm": [null, null]},
    {"image": "fit_y.jpg", "offset_mm": [null, null]}
  ],
  "check": [
    {"image": "check_1.jpg", "offset_mm": [null, null]},
    {"image": "check_2.jpg", "offset_mm": [null, null]},
    {"image": "check_3.jpg", "offset_mm": [null, null]},
    {"image": "check_4.jpg", "offset_mm": [null, null]}
  ],
  "reject": [
    {"image": "absent.jpg", "reason": "absent"},
    {"image": "occluded.jpg", "reason": "occluded"}
  ]
}
```

Patch boxes are `[left, top, right, bottom]` pixels in the **taught reference**.
Keep immutable measurements and photos with the generated profile. Profiles bind
to exact episode metadata, reference image, camera identity, image resolution,
frame and evidence hashes. Changing them requires a new profile and rehearsal.

## Build, enable and check offline

Build one profile per object after filling the measurement files:

```sh
.venv/bin/python -m runtime --config config/local.json vision-demo-profile coconut_water \
  runs/vision_demo_setup/coconut_water.measurements.json --out runs/vision_demo_setup/coconut_water.profile.json
.venv/bin/python -m runtime --config config/local.json vision-demo-profile pitcher \
  runs/vision_demo_setup/pitcher.measurements.json --out runs/vision_demo_setup/pitcher.profile.json
```

Merge into `primitive_settings` of ignored `config/local.json` only after both
commands pass:

```json
"replay_vision": {
  "profiles": {
    "coconut_water": "runs/vision_demo_setup/coconut_water.profile.json",
    "pitcher": "runs/vision_demo_setup/pitcher.profile.json"
  }
}
```

A nonempty profile map opts into vision for the entire signature demo. A missing
profile blocks both stages before connection; there is no silent manual fallback.
Absent/empty configuration retains the operator-measured-offset path.

```sh
.venv/bin/python -m runtime --config config/local.json vision-demo-check coconut_water runs/NEW_IMAGE.jpg
.venv/bin/python -m runtime --config config/local.json replay-demo --compact
.venv/bin/python -m runtime --config config/local.json prepare-demo --compact --out runs/demo1_vision_v1
.venv/bin/python -m runtime --config config/local.json prepared-demo runs/demo1_vision_v1
```

`vision-demo-check` evaluates a saved file and prints diagnostic offsets; it does
not establish freshness or authorize motion. Live replay always captures a new
image, records match evidence, and refuses observations older than 30 seconds.
Weak/ambiguous matches, disagreement between patches, incorrect resolution or
out-of-region translations stop the routine before pickup. Failure never falls
back to a guessed zero offset or widens a limit. Camera mounting and fixed object
orientation/height still require observation; patch agreement alone cannot prove
those conditions.

The usual observer UI uses vision automatically when started with this config
and a newly frozen vision pack. For an explicitly authorized rehearsal, launch
with `--demo-pack runs/demo1_vision_v1 --enable-demo-execution` in the operator
terminal. Rehearse both sources and require two consecutive complete observed
successes before calling the routine ready. Software tests and same-image matches
are not physical grasp evidence.
