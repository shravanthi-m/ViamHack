# Varista · Claudia’s counter

A local presentation for **Varista**, starring **Claudia**, the robot bartender.
It includes station snapshots, optional OpenRouter object observations, run activity,
and voice/text drink requests. The default preview never connects for motion.
Start from the repository root:

```sh
.venv/bin/python -m runtime observer
```

Open http://127.0.0.1:8765. Load a station photo to preview it. The observer has no
network dependency until you choose a camera snapshot or scene analysis.

For configured station snapshots and an existing run's events:

```sh
.venv/bin/python -m runtime --config config/local.json observer \
  --view overhead --run runs/EXACT_RUN_DIRECTORY
```

`--view` must name a declared camera view. Capture snapshot connects only on click,
reads an image, and closes its connection. Images are snapshots, not a live video
stream. `--image PATH` can preload a saved JPEG, PNG or WebP. Uploads stay in
memory; station captures use the camera primitive's normal ignored image directory.
The observer binds only to localhost.

`--run` pins the exact directory containing `events.jsonl` and `result.json`,
including prepared-demo/replay run directories. It never guesses the latest run.
Mock logs are labeled MOCK RUN. Replay logs without an explicit mode are labeled
RECORDED RUN; their files do not establish whether a robot is currently connected.
Completed commands are displayed as reported run status, not independent proof of
physical success. To attach a newly created run, restart with that exact directory.

## Optional scene understanding

Add these locally to the ignored `.env` file. The server rereads them automatically:

```dotenv
OPENROUTER_API_KEY=your-local-key
OPENROUTER_VISION_MODEL=qwen/qwen3-vl-30b-a3b-instruct
OPENROUTER_STT_MODEL=openai/whisper-1
```

Analyze scene sends the displayed image to OpenRouter and the selected model
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

## Voice and presentation

Tap the mic to enable Claudia’s sassy spoken replies and start a voice turn. With
an OpenRouter key, tap again to finish recording (30 seconds maximum). Audio goes
to OpenRouter using `OPENROUTER_STT_MODEL`. Otherwise browser speech recognition
ends the turn after you pause; this may use the browser’s online speech service.
Test microphone permissions in the presenting browser. Typed input remains available.

Try “Hello Claudia,” “how are you,” “what can you do,” “tell me a joke,” or “thank you.”
Claudia answers these phrases automatically. This is a small, fixed conversation
repertoire, not an open-ended language model chat.

**Enable sassy voice** previews her voice without opening the mic. The selector
offers available English browser voices, preferring Samantha or another named voice
when available. Slightly brisk pacing, bright pitch, and cheeky copy create the
personality; the exact sound depends on the installed/browser voices. Changing the
voice plays a sample. Click **Sassy voice on** to mute replies. Starting another mic
turn enables replies again and interrupts current speech to avoid feedback.

“Claudia, what do you see?” triggers optional image analysis. Say or type
**“Claudia, help pour a drink”**, then send, or click **The signature pour**.
Claudia selects one fixed routine: coconut pour → carton return → pitcher pour →
pitcher return. Preview shows the recipe and explicitly reports no motion.
Unsupported requests (including amounts or extra actions) cannot launch a script.
Transcribed speech uses a review-only request: chat replies are automatic, and scene
questions can analyze the displayed image. A drink request stays in the input with
**REVIEW REQUEST · PRESS SEND** and cannot start the routine. Review it, then press
send to submit it. Existing terminal operator gates still apply in supervised mode.

## Enable supervised execution

For an authorized physical show run, prepare and rehearse a new frozen pack after
source changes as described in [the Script 1 runbook](demo1_runbook.md). In an
interactive operator terminal, start:

```sh
.venv/bin/python -m runtime --config config/local.json observer \
  --demo-pack runs/demo1_v2 --enable-demo-execution
```

Use the exact pack path you prepared; the example directory must already exist.
The header reads **SUPERVISED ROBOT MODE**. Sending the signature request launches
only that pack. The first terminal confirmation checks intent, saved home, empty
hand, fixed cup, free-drive off, and clear paths before connecting. All existing
placement measurements, grasp, support/release, and outcome gates remain in the
terminal. The current recordings still require manual gripper closure. This is
a supervised routine, not an unattended one-command pour.

The display automatically follows only this request’s run directory and shows
when Claudia is waiting for the operator. Duplicate requests are blocked while
a routine is active; failures require inspection/reset and a server restart.
There are no automatic retries. Ctrl-C cancels the worker through the existing
replay StopAll/connection-close path. Do not run another robot controller alongside
this process; the station has no cross-process robot lock. No execution flag means
preview only, even if a pack path is provided. Pack integrity is checked at startup,
at request time, and again before connection. A stale pack is rejected.

The full routine remains a candidate until the runbook’s observed physical trials
are complete. UI tests and software validation do not establish pouring success.

Before presenting: load an actual station image, attach the intended run, test the
microphone in the presenting browser, and use the full-screen button. Rehearse
OpenRouter latency/quality on actual station images before depending on its boxes.
If vision credentials are unavailable, use the snapshot and activity display.
Verify/rebuild prepared packs after integration source edits as required by their
integrity checks; preparation does not replace physical rehearsal.

References: [OpenRouter image inputs](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding),
[structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs),
[model](https://openrouter.ai/qwen/qwen3-vl-30b-a3b-instruct),
[browser speech recognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition),
[browser voices, pitch and rate](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisUtterance).

Voice controller regression tests (offline): `node --test tests/test_observer_voice.cjs`.
