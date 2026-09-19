# OpenRouter, localization, and the presentation

The key is read from the local ignored `.env`, never sent to the browser or logged.
The observer rereads it, so adding the key does not require a restart. Process
environment variables override `.env`. Defaults:

```dotenv
OPENROUTER_API_KEY=your-local-key
OPENROUTER_VISION_MODEL=qwen/qwen3-vl-30b-a3b-instruct
OPENROUTER_STT_MODEL=openai/whisper-1
```

Qwen supplies object boxes. Whisper transcribes short voice requests. Spoken replies
use the browser's speech synthesizer. Voice is tap-to-record/tap-to-finish, capped
at 30 seconds; the transcript remains editable and is not submitted automatically.
Browser speech recognition remains the fallback when no key is configured.

## What is implemented

The shared detector in `primitives/vision.py` is used by both the observer and
`localize`. It asks Qwen for a 0..1000 coordinate grid, validates it, then normalizes
boxes to 0..1 for display and projection. It rejects invalid/inverted boxes,
duplicate identities, substantially overlapping identities, incomplete responses,
bad image data and EXIF-rotated images. No model-generated motor coordinates are used.

The observer and saved-image diagnostic default to the presentation's three props:
`coconut_water,pitcher,cup`. Use `--objects` for a different configured subset.
This is an explicit detection vocabulary, not evidence that omitted objects are
absent. The single-target `localize` call asks only for its requested identity.

Live API checks on September 19:

- Qwen returned boxes for the actual pitcher, cup and coconut carton in an approved
  saved 1280 × 720 station image. Result: `runs/openrouter_demo_objects.json`.
- Asking for all configured objects produced a false coffee label. The earlier
  outputs remain in `runs/openrouter_station_check*.json`; recognition is still a
  candidate, not validated grasp evidence. Missing-object and occlusion testing
  remain necessary before relying on this detector for manipulation.
- Whisper returned a transcript of a synthetic voice clip. It heard “pitcher” as
  “pitch your”; review/correction is part of the interface. A real microphone test
  at the presentation venue remains necessary.
- The exact presentation phrase, synthesized as browser-format WebM audio,
  transcribed as “Claudia! Help pour a drink!” and parsed as `signature`, without
  invoking execution. The combined offline suite passed 282 tests.

## The localization boundary

There are two different operations:

1. **Image localization:** “The carton is in this rectangle.” Available through
   OpenRouter now. This powers the audience overlay.
2. **Robot localization:** “This object has a usable grasp pose in millimetres.”
   The `localize` implementation is wired, but needs measured geometry first.

The runtime's exact `Target` contract is preserved: `object_id`, `frame`, `pose`.
For robot localization, a fresh camera image is detected and a profile-defined
anchor within the box is projected through the homography. A measured XY anchor
offset, height, and Viam orientation complete the pose. A bare box center is not
assumed to be a grasp point. Physical setup must preserve the profile's tested
orientation and height; this implementation does not estimate rotation or depth.

The present station is blocked because:

- `config/overhead_homography.json` has **27.01 mm mean / 67.85 mm maximum error**.
  A tighter detector does not remove camera-to-robot transform error.
- The artifact does not record the image resolution at which it was measured.
  The test photo is 1280 × 720, but that does not establish calibration resolution.
- No validated per-object manipulation profiles exist.

Run this offline for the current configuration status:

```sh
.venv/bin/python -m runtime --config config/local.json localization-status
```

## Exactly what is left for the short presentation

Use the **taught, fixed-layout routine** for the robot and OpenRouter for its visible
perception/voice layer. This avoids making the presentation wait for new calibration.

1. **Start the UI with the real station config and overhead view.** Use the existing
   observer launch described below. Check the photograph reflects the current scene.
2. **Test a real voice request in the presenting browser.** Grant microphone access,
   say “Claudia, help pour a drink,” stop recording, check the transcript, and send.
   The default server is preview mode; execution requires the separate explicit
   opt-in and operator gates described in `docs/observer.md`.
3. **Capture and analyze the actual demo layout.** Keep only the three demo props in
   the scan vocabulary. Verify the boxes visually; try a missing prop and an
   occluded prop. If it invents an object, do not use the result for robot motion.
4. **Prepare a fresh frozen pack after all code edits finish.** Existing packs hash
   runtime/primitive sources and dependencies, so changed source invalidates them.
   Preparation is offline; use a new output directory, then verify it.
5. **Rehearse the physical two-pour routine when explicitly authorized.** Establish
   saved home, empty hand, fixed cup, source positions/orientations, fill levels,
   return spots and full-path clearance. Existing routines require manual gripper
   closure at taught settings and measured placement confirmation. Keep an operator
   at the terminal for each gate. No automatic retry after a partial pour.
6. **Get two consecutive complete observed successes of the same frozen routine.**
   Both pours, stable cup, no spill/contact, upright returns and empty hand. Then
   restore the scene and rehearse the presenter cues. Keep the camera/voice demo as
   a fallback if the physical routine has not passed.

Suggested 60–90 second story: “We taught Claudia these motions.” → voice request →
camera snapshot and object boxes → operator-admitted taught pours → observed finish.
Describe the AI as recognizing the scene and accepting the request. The robot is
following a taught routine with supervision; do not claim arbitrary-pose autonomy.

## What is additionally needed for vision-guided grasping

The primitive/calibration owner needs to supply these measured artifacts, not just
a key or a setting that relaxes the existing limits:

1. Establish the exact camera resolution and unchanged camera mounting. Measure a
   transform on the relevant plane and independently check errors across the actual
   working area. Save measured `image_size: [width, height]`, frame, units and errors
   in a new versioned calibration artifact; do not relabel the old measurement.
2. For each prop, define the intended manipulation anchor and measure its relationship
   to the detector box: `bbox_anchor: [u,v]`, task-frame `offset_xy_mm`, `z_mm`, and
   `orientation: {o_x,o_y,o_z,theta}`. Fixed orientation/height is a scope restriction,
   not a model estimate. The cup's pour target and a container's grasp anchor are
   different physical contracts.
3. Validate the **whole detector-to-target chain** against independent measured
   targets over a declared XY region, including repeated shots, missing/occluded
   objects, and the station lighting. Use the selected model and `bbox-1000-v1`
   detector. Record worst positional error and the primitive owner's approved grasp
   tolerance; verify height/orientation separately. Preserve evidence of failures.
4. Fill a copy of `config/localization_profile.example.json` with measurements.
   The example intentionally contains nulls and is a candidate. Its `max_error_mm`
   is the measured end-to-end positional error, not model confidence or just the
   homography residual. `validated_region_mm` holds increasing `x` and `y` bounds.
   `evidence` names the physical measurement record. Pin `calibration_sha256` to the
   exact calibration bytes and `camera_resource` to the actual configured camera.
5. Reference only validated profiles under
   `primitive_settings.localization.target_profiles` in ignored local config:
   `{"coconut_water": "config/MEASURED_PROFILE.json"}`. A matching calibration must
   already appear in `homographies`. Do not widen tolerance to make the gate pass.
6. Validate the intended plan offline with `validate PLAN --executable`. Localization
   profiles are checked before connecting. The generic `pour`, spoon pickup,
   insertion, stirring and return primitives remain unimplemented; enabling localize
   does not make those tools executable. The taught replay is a separate path and
   still uses operator-measured offsets; adapting it to vision offsets needs its own
   reviewed/rehearsed adapter.

At runtime, absent/partial/uncertain objects, stale images, resolution/model/camera/
calibration mismatches, excessive measured error and out-of-region/workspace targets
all fail. Successful localizations keep the original Target shape and save separate
`.localization.json` evidence next to the captured image. Configuration readiness
never establishes current physical scene readiness or successful pouring.

## Launch and diagnostic commands

```sh
# Audience UI, with camera capture on click; default preview mode.
.venv/bin/python -m runtime --config config/local.json observer --view overhead

# Replay the approved saved image and its real OpenRouter boxes without another API call.
.venv/bin/python -m runtime --config config/local.json observer \
  --image runs/images/20260919T025625-79f97257-overhead-overhead-cam.jpg \
  --observation runs/openrouter_demo_objects.json

# Diagnostic: sends this saved image to OpenRouter, never connects to the robot.
.venv/bin/python -m runtime --config config/local.json perceive YOUR_IMAGE.jpg \
  --out runs/NEW_OBSERVATION.json

# Prepare only after code stops changing; neither command moves anything.
.venv/bin/python -m runtime --config config/local.json prepare-demo --compact \
  --out runs/demo1_openrouter_v1
.venv/bin/python -m runtime --config config/local.json prepared-demo runs/demo1_openrouter_v1
```

Sources: [Qwen model](https://openrouter.ai/qwen/qwen3-vl-30b-a3b-instruct),
[OpenRouter transcription contract](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/),
[Whisper model](https://openrouter.ai/openai/whisper-1).
