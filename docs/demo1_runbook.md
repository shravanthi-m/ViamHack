# Script 1: coconut pour, then pitcher pour

Start at the saved home with an empty gripper. Pick up coconut water, pour into
the fixed cup, return the carton; pick up the pitcher, pour into the same cup,
return the pitcher. Finish after its recorded retract, with empty gripper.
Cup handling and shaking are separate future scripts. Spoon handling is out of
scope. The user confirmed the existing source recordings suffice for Script 1.

This is a candidate, not a physically validated complete routine. Execution is a
fixed program with operator observations and gates; it needs no language-model
composition during the demo. Software checks cannot replace missing observations.

## Start the prepared script

From the repository root, this verifies the exact prepared data and dependencies
without connecting or moving:

```sh
.venv/bin/python -m runtime --config config/local.json prepared-demo runs/demo1_v1
```

For an explicitly authorized physical rehearsal or show run:

```sh
.venv/bin/python -m runtime --config config/local.json prepared-demo runs/demo1_v1 --execute
```

The executor prompts for source placement/clearance, measured offset, grasp,
support before release, and pour/return outcomes. Existing episodes require
manual gripper closure at the saved settings: coconut 10%, pitcher 20%. It never
asks a language model to invent the next motion. It does not automatically return
home at the end, release on failure, or retry a failed pour.

## Frozen data

The pack uses copies of the selected metadata, compact pose tracks and checkpoint
images. It hashes runtime/primitive sources, requirements, local task config and
referenced homographies. Changed or missing dependencies block verification.
Later teaching cannot silently change the selected episode. Original phase images
and logs remain at their source paths; packs belong under ignored `runs/`.

To prepare a new revision offline, use a new output directory:

```sh
.venv/bin/python -m runtime --config config/local.json prepare-demo --compact --out runs/demo1_v2
```

A new pack remains a candidate until rehearsed. The manifest is an integrity
record, not a physical certificate. Installed dependencies and machine-side
configuration must also remain the same across rehearsal and show; file hashes
cannot detect a remote machine change.

## Starting-state decisions

| Observed state | Prepared response |
| --- | --- |
| Home, empty hand, original source orientations, fixed cup, clear full swept paths | Admit the rehearsed program after measured placement confirmation |
| Source translated reliably within 20 mm XY magnitude / 10 mm Z | Existing replay can translate pickup/lift only; rehearse the variant before claiming coverage |
| Source rotated, cup moved, different object or grasp geometry | Restore marked setup or prepare/rehearse a new candidate |
| Pitcher obstructs coconut fingers | Reset layout and re-observe the entire open-finger swept path |
| Gripper loaded, arm away from home, or stopped state unknown | Block entry; operator establishes/reset state; do not auto-open or send a loaded arm home |
| Interrupted or partial pour | Stop, inspect source/cup contents, reset; never automatically repeat the pour |
| Contact, lost grasp, or connection loss during motion | Stop; retain evidence; establish stopped/held state; operator reset; no blind resend |
| Either source track missing, changed or invalid | Block both stages before any motion |

Offset limits are software limits, not a physically demonstrated range. Capturing
an image does not localize an object. The current overhead homography alone cannot
establish precise 3D grasp offsets. Sources return to their original taught spots,
which must be clear even when their pickup positions differ.

## Resources needed now

1. One operator for source setup, gripper controls and observable outcome checks.
2. Placement marks for both source footprints and orientations, the fixed cup and
   return spots. Verify full finger clearance around the neighboring source.
3. Repeatable source fill levels and sufficient receiving-cup capacity for both
   taught pours. Recorded timing does not provide volume control.
4. Authorization for observed rehearsals. Proposed budget: up to six complete
   attempts, zero automatic motion retries; two consecutive successful complete
   runs of one frozen revision before calling Script 1 ready.
5. If manual closure is unacceptable, primitive-owner integration of the observed
   atomic force-limited command. The generic gripper primitive still requires a
   torque readback this station has not supplied. An empty setter response does
   not prove the force was set. No new recordings are requested for preparation.

## Remaining two-hour preparation window

| Minutes from team start | Work | Exit criterion |
| --- | --- | --- |
| 0–15 | Mark setup; inspect clearance and fills; verify frozen pack | Reproducible starting scene |
| 15–45 | Authorized first rehearsal of both sources | Actual pours and returns observed |
| 45–80 | Address observed failures only; revise/record if necessary | One complete working candidate |
| 80–110 | Freeze; two consecutive full observed trials | Same script/setup, no edits between successes |
| 110–120 | Restore scene; verify package; rehearse presenter/operator cues | Ready to run without improvisation |

Do not lower acceptance criteria after failures. A failed or unknown gate is a
reset or a reported blocker, not an invitation to improvise during the show.

## Show cues

Presenter: “The robot pours coconut water, returns the carton, then pours from
the pitcher and returns it.” Make this claim after full rehearsal passes.

Operator: confirm home/empty and setup; enter reliably measured offsets (zero
only when verified); manually close at saved force; confirm intended grasp;
observe the pour and upright return; confirm physical support before release.
Before the pitcher stage, check its scene again. At completion, verify both
sources returned upright, cup stable and gripper empty.

On failure: “We are resetting the station.” Follow the matching state row.
Do not run `put-back` as a generic reset: it moves unless `--dry-run` is passed
and does not establish identity or clearance for an unknown held object.

## Readiness evidence

Record run directories, elapsed time, fill levels, force settings, operator
observations, failures and reset counts in `runs/fix_loop/demo1/`. Require two
consecutive full successes for the same frozen revision: both pours, stable cup,
no observed spill/contact, upright source returns and empty hand. Missing outcome
observations are unknown. Preserve unsuccessful revisions and original evidence.
