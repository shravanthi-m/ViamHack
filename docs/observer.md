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
The default scan vocabulary is the demo's coconut carton, pitcher, cup and the
display-only shaker and honey bottle. Honey recognition uses the reference bottle's
yellow cap, tall dark amber/brown body and ribbed sides; it boxes the whole visible
bottle. Pass `--objects id,id,...` to select a different subset of configured objects
or the display-only `shaker` and `honey` labels.

The display model fills one nullable slot per allowed object ID. Absent objects
and identities with multiple indistinguishable instances are omitted, so other
identifiable objects can still be shown. Unknown labels, repeated identity slots
and conflicting labels for the same object are also omitted without aborting.
The API response contains normalized image boxes, allowed object IDs, qualitative
visibility and a summary of the displayed objects. Local validation rejects
invalid boxes, malformed response structures and incomplete responses. Empty
detection lists are valid. Boxes are approximate model output, not measured
geometry. New images clear old detections, and results are tied to the analyzed frame.

The display scanner has a separate detector version from the implemented
`localize` primitive; existing localization profiles cannot use display results.
Robot localization remains blocked on this station's measured object profiles and calibration:
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
- **Reset** asks the operator on the webpage to identify an empty hand, coconut carton,
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
The page provides on-demand object scanning and operator checks alongside task controls.

## Enable supervised execution

For an authorized physical show run, prepare and rehearse a new frozen pack after
source changes as described in [the Script 1 runbook](demo1_runbook.md). Start:

```sh
.venv/bin/python -m runtime --config config/local.json observer \
  --demo-pack runs/demo1_v2 --enable-demo-execution
```

Use the exact pack path you prepared; the example directory must already exist.
The header reads **SUPERVISED ROBOT MODE**. Each task uses the exact pack/config
and operator entry gates on the webpage. For Signature, the first confirmation checks intent, saved home, empty
hand, fixed cup, free-drive off, and clear paths before connecting. All existing
placement measurements, grasp, support/release, and outcome gates appear in an
**Operator check** form above the task buttons. Enter the requested answer and
press **Continue**, or choose **Abort task**. Each answer is bound to the current
check; stale and duplicate submissions cannot advance another check. The configured `ufactory_atomic` adapter closes automatically using
[per-object settings](configuration.md#automatic-closure-and-object-settings);
without that opt-in, operator-entered recordings require manual closure. This is
a supervised routine, not an unattended one-command pour.

After the final outcome confirmation and worker cleanup, the page displays
**Ready for another task**, clears the old task feedback, and re-enables task
buttons and custom input without a reload. A task awaiting its final outcome
confirmation is still active.

The task feedback follows the current routine, including after a page reload,
and shows when Claudia is waiting for the operator. A missing answer leaves the
routine waiting; reconnecting does not auto-confirm any check. Duplicate requests are blocked while
a routine is active. After a failure or operator abort, use **Clear stopped task**
on the page: identify the held state and confirm that you inspected the station.
The server checks fresh arm and gripper feedback without motion. It refuses a
moving arm, mismatched/unknown held state, unavailable feedback, or changed pack.
If loaded or away from home, only Reset is allowed until it completes. Otherwise
you can select a fresh task. **Continue with Reset** starts the supervised recovery
checks when Reset is required; it never resumes or repeats the interrupted pour.
Predicted wrist/camera collisions are shown explicitly instead of a generic
failure message. Collision-blocked motion needs path/geometry review before a
fresh pour. The old run remains failed in its evidence files.
There is no mid-routine resume; clearing never repeats or continues old steps.
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

When upgrading/restarting an idle server that already requires recovery, preserve
that restriction with `--require-reset`; only Reset will be accepted until its
observed completion. Do not restart an active routine to clear its state.
