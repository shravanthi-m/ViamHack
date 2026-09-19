# Fixed demo tasks

The three website buttons share the same supervised handlers as the CLI scripts.
An operator must keep the launching terminal open. Default launch/checks never
connect for motion. These are candidates until rehearsed on the station.

| Task | Required start | Behavior |
| --- | --- | --- |
| Signature pour | Saved home, empty hand, taught setup and fixed cup | Coconut pour/return, then pitcher pour/return; existing replay gates |
| Reset | Known stopped state; empty hand or identified coconut carton/pitcher in its taught grip | Operator chooses identity, gripper checks held state; return to the taught place with a support-before-release gate, then home. Empty hand goes directly home. |
| Pick up and shake (website button) | Empty gripper, shaker at taught location, reviewed starting/approach path | Uses the taught book: approach, grip, lift, root shake_full, supported placement, release and retreat. |
| Shake held object | Securely side-gripped sealed object already lifted, levelling/oscillation space clear | Root `shake_full.shake` for 3 seconds; finishes still holding at the levelled centre. No finding, pickup, placement, or release. |

The UI does not call the older `primitives.shake` or root `run_sequence`.
All `primitive_settings.shake` fields must be explicitly reviewed/configured;
resolved settings and the root 90 deg/s stroke request print before admission.
The frozen pack now includes root `shake_full.py`; old packs must be prepared
again, not edited to bypass drift checks. Preview mode deliberately never moves
the robot. Supervised mode waits for the launching terminal's operator gate.
Runtime failures now appear in the UI, and block further tasks until inspection
and restart. A failed StopAll requires physical-stop intervention, not a UI retry.

The separately taught pickup/return routine completed once on 2026-09-19 with
operator confirmation. An unchanged repeat reported self-collision on approach
and StopAll failed; the operator subsequently reported the arm stationary.
Pickup repeatability is therefore NOT established. The full-pickup button is
integrated but no UI hardware trial was performed. Resolve the fault
and recheck the path before any physical task, including held-object shaking.

For the full button, set `taught_shake_book` in local config to the reviewed pose
book, for example `runs/shake-poses.json`. The book is validated and its hash is
pinned when preparing the pack, then checked again after the terminal gate.
Missing poses/settings, an unverified station, or changed files block connection.
The local book's station verification was revoked after the collision fault;
restore it only after reviewing the actual stopped state and approach path.
Do not upload pose books, local config, credentials or raw run evidence to Git.
The held-only action remains available via the text request `Shake held object`
or CLI task `shake`; the new full routine uses task `shaker`.

Unknown objects cannot use Reset. After a shake, an operator must handle an object
without a taught return (including the shaker). Never select pitcher/carton to
reset a different object. Failure blocks further UI tasks until inspection/reset
and a server restart. There are no automatic recovery attempts.

## Prepare once after source/config edits stop

From the repository root:

```sh
export DEMO_PACK="runs/fixed_show_$(date +%Y%m%d_%H%M%S)"
sh demos/prepare_fixed.sh "$DEMO_PACK"
```

Preparation creates a new frozen candidate and checks all three tasks offline.
Do not edit code/config/calibration after rehearsal; changed dependencies reject
the pack. Keep the same `DEMO_PACK` environment variable in the terminal below.

## Website

Stop the old observer with Ctrl-C in its terminal. A server started before the
task endpoint was added can serve new HTML while returning `Not found.` to task
requests. The UI now detects an incompatible server and asks for a restart.

```sh
# Preview only, with the overhead camera:
sh demos/start_ui.sh

# For an authorized physical run, from an interactive operator terminal:
sh demos/start_ui.sh --execute
```

Open http://127.0.0.1:8765 and reload. Execution requires the header to say
`SUPERVISED ROBOT MODE`. Choose a task and answer its terminal gates. For Reset,
enter `empty`, `coconut_water`, or `pitcher`. Type `q` to abort a gate.
Do not run CLI tasks alongside an executing website.

## Standalone scripts

```sh
# Offline checks (also the default when no second argument is provided):
sh demos/fixed_tasks.sh signature --check
sh demos/fixed_tasks.sh reset --check
sh demos/fixed_tasks.sh shake --check

# Physical runs; each retains the same terminal gates as the website:
sh demos/fixed_tasks.sh signature --execute
sh demos/fixed_tasks.sh reset --execute
sh demos/fixed_tasks.sh shake --execute
```

`DEMO_CONFIG` optionally selects a full local config; it must match the pack.
`DEMO_PORT` optionally changes the website port. No script changes machine config,
teaches new geometry, guesses object identity, or starts motion as an offline check.
