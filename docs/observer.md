# Varista · Claudia’s counter

A local presentation for **Varista**, starring **Claudia**, the robot bartender.
The UI keeps the front page and open counter, with a camera scene, three preset
task buttons, and one custom-task input. The default preview never connects for motion.
Start from the repository root:

```sh
.venv/bin/python -m runtime --config config/local.json observer
```

Open http://127.0.0.1:8765. The overhead camera stream starts automatically when
the page opens. Camera access is read-only; preview mode does not enable motion.

To attach an optional existing run to the API:

```sh
.venv/bin/python -m runtime --config config/local.json observer \
  --view overhead --run runs/EXACT_RUN_DIRECTORY
```

`--view` defaults to `overhead` and must name a declared camera view.
`--image PATH` can still preload a saved JPEG, PNG or WebP as a fallback while
the stream connects. The observer binds only to localhost.

`--run` pins the exact directory containing `events.jsonl` and `result.json`,
including prepared-demo/replay run directories. It never guesses the latest run.
Mock logs are labeled MOCK RUN. Replay logs without an explicit mode are labeled
RECORDED RUN; their files do not establish whether a robot is currently connected.
Run state remains available through `/api/state`; the simplified UI does not show
the run timeline. Completed commands are not independent proof of physical success. To attach a newly created run, restart with that exact directory.

## Live camera and recurring identification

Start with a configured camera view (no motion is enabled):

```sh
.venv/bin/python -m runtime --config config/local.json observer --view overhead
```

Opening the page starts the stream automatically. The server keeps one read-only Viam camera connection and
polls `get_images` at up to five frames per second, publishing a JPEG stream to the
browser. Frames are resized within 960 × 720 and kept in bounded memory, not saved
to disk. The actual FPS and frame age are displayed. Camera/network latency can
reduce the rate; this is not a high-frame-rate WebRTC feed.

The simplified UI starts live view with `identify: false`: it shows the camera scene
without automatic model requests. **Scan objects** analyzes one fresh overhead
frame on demand and shows labeled bounding boxes on that exact still image below
the live feed. The live feed continues while scanning. Each click makes one model
request; repeated scans replace the previous result. Empty detections and errors
appear beneath the button.
There are no manual live-view, snapshot, or image-upload buttons. Camera failures
are retried automatically at most once every ten seconds while the page is visible.

A hidden/closed page stops heartbeats;
the camera stops within 20 seconds unless another visible viewer keeps the shared
session alive. Returning to the page automatically reconnects an expired stream.
Starting/stopping requires same-origin POSTs; loading a stream URL
never initiates a camera connection.

The scan uses `/api/scan` and retrieves the exact analyzed image through
`/api/live/identified`. Boxes resize with the image and include object labels;
partial or uncertain detections are labeled accordingly. Results are cleared when
the camera session changes or the website loses its connection.
Offline verification: `python -m unittest discover -s tests -v` and
`node --test tests/test_observer_ui.cjs`. Tests use fake cameras and mocked hardware.

## Optional scene understanding

Add these locally to the ignored `.env` file. The server rereads them automatically:

```dotenv
OPENROUTER_API_KEY=your-local-key
OPENROUTER_VISION_MODEL=qwen/qwen3-vl-30b-a3b-instruct
OPENROUTER_STT_MODEL=openai/whisper-1
```

The `/api/scan` endpoint sends the selected image to OpenRouter and the selected model
provider. The key never reaches the browser. The default model supports image
inputs and JSON schema responses; it can be changed through the environment.
The default scan vocabulary is the demo's coconut carton, pitcher and cup. Pass
`--objects id,id,...` to select a different subset of configured objects.

The response contains normalized image boxes, allowed object IDs, qualitative
visibility and a summary. Local validation rejects invalid boxes, unexpected
fields, duplicate identities and incomplete responses. Empty detection lists are
valid. Boxes are approximate model output, not measured geometry. New images clear
old detections, and results are tied to the analyzed frame.

The detector is shared with the implemented `localize` primitive, which remains
blocked on this station's measured object profiles and calibration:
the saved overhead homography reports 27.01 mm mean / 67.85 mm worst error, maps only
the calibrated plane, and does not supply object height or grasp orientation.
Bounding-box centers, model confidence and typed poses cannot fill those gaps.
Robot localization still needs a measured manipulation anchor, suitable calibration
and observed accuracy within the primitive owner's grasp tolerance. Marker-based
measurement or depth plus calibrated transforms can be evaluated separately.
See [the exact remaining demo and localization checklist](openrouter_demo_checklist.md).

## Task controls

- **Pour signature drink** requests coconut pour → carton return → pitcher pour →
  pitcher return through the existing supervised routine.
- **Reset** asks the terminal operator to identify an empty hand, coconut carton,
  or pitcher. It returns an identified held object to its taught place, gates
  release on observed support, then returns home empty. Unknown objects block it.
- **Shake held object** shakes a securely side-gripped, sealed, already lifted
  object for three seconds. It finishes still holding; no pickup or return.
- The custom-task input sends the user's text to the same request endpoint.

All three fixed tasks use supervised execution integrations. The exact scripts,
starting conditions, and launch commands are in [Fixed demo tasks](fixed_tasks.md).
Unsupported custom tasks display **TASK NOT CONNECTED** and preserve the text for
editing. Preview mode describes each task without moving the robot. Amount control,
shaker finding/pickup/return, and extra actions remain unsupported.
Voice controls, scene-analysis controls, recipe details, and the activity timeline
are no longer part of the page. The backend endpoints are unchanged.

## Enable supervised execution

For an authorized physical show run, prepare and rehearse a new frozen pack after
source changes as described in [the Script 1 runbook](demo1_runbook.md). In an
interactive operator terminal, start:

```sh
.venv/bin/python -m runtime --config config/local.json observer \
  --demo-pack runs/demo1_v2 --enable-demo-execution
```

Use the exact pack path you prepared; the example directory must already exist.
The header reads **SUPERVISED ROBOT MODE**. Each task uses the exact pack/config
and terminal entry gates. For Signature, the first confirmation checks intent, saved home, empty
hand, fixed cup, free-drive off, and clear paths before connecting. All existing
placement measurements, grasp, support/release, and outcome gates remain in the
terminal. The configured `ufactory_atomic` adapter closes automatically using
[per-object settings](configuration.md#automatic-closure-and-object-settings);
without that opt-in, operator-entered recordings require manual closure. This is
a supervised routine, not an unattended one-command pour.

The task feedback follows this request and shows when Claudia is waiting for the
operator. Duplicate requests are blocked while
a routine is active; failures require inspection/reset and a server restart.
There are no automatic retries. Ctrl-C cancels the worker through the existing
replay StopAll/connection-close path. Do not run another robot controller alongside
this process; the station has no cross-process robot lock. No execution flag means
preview only, even if a pack path is provided. Pack integrity is checked at startup,
at request time, and again before connection. A stale pack is rejected.

The full routine remains a candidate until the runbook’s observed physical trials
are complete. UI tests and software validation do not establish pouring success.

Before presenting: verify that the overhead stream starts and check task controls in
preview mode. Use the full-screen button if needed.
Verify/rebuild prepared packs after integration source edits as required by their
integrity checks; preparation does not replace physical rehearsal.

References: [OpenRouter image inputs](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding),
[structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs),
[model](https://openrouter.ai/qwen/qwen3-vl-30b-a3b-instruct).

UI controller regression tests (offline): `node --test tests/test_observer_ui.cjs`.
