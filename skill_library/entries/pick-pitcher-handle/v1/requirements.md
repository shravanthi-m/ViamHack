# Requirements for physical learning

This is a missing-capability specification for the human primitive/perception
authors, not a list of available tools. Preserve the explicit registry and target
contract when integrating their implementations. Keep robot algorithms in
`primitives/` and phase execution in `runtime/`. Do not change machine limits or
calibration to get a trial admitted.

## Minimum implementation and measurement package

1. **Horizontal approach and lift.** Expose a supported primitive contract that
   accepts a verified handle grasp target and measured approach parameters, or a
   reviewed full-pose motion interface. Verify tool pose, task-frame workspace,
   reachability, calibrated tool axis, finger orientation, and swept-volume
   clearance. Constrain the final insertion to a horizontal line at constant
   attitude; a sequence of unconstrained endpoint moves is insufficient evidence.
   Report reached poses, tracking error and partial progress on failure. A held
   pitcher changes the swept volume during lifting and transport.
2. **Handle localization.** Return a fresh handle pose in the task frame, object
   identity, observation references, timestamp, and a justified uncertainty bound.
   Establish support height, selected contact segment, opening dimensions, and
   handle heading. Use calibrated camera intrinsics/extrinsics and verified depth,
   or a measured object model with a validated pose estimator. The current planar
   mapping and reported sample maximum error are not a bound for a raised handle.
3. **Taught grasp geometry.** Measure finger width/thickness, actual open aperture,
   tool-reference-to-finger offsets, handle opening, contact segment thickness,
   insertion depth, pregrasp standoff, grasp roll, and body/tray clearance. Record
   `T_handle_gripper`, not just the robot's world pose at one table location. Check
   that the selected grasp really places the intended finger through the opening.
4. **Grip and load.** Establish pitcher mass/fill class and a force that holds it
   without deformation within existing limits. The current default force ceiling
   is 30%; it is neither a recommended starting force nor evidence of a safe grasp.
   Preserve verified force readback and required holding checks.
5. **State and outcome observation.** Provide read-only gripper holding feedback
   before opening and after lifting, and visual/sensor evidence of finger contact,
   support separation, uprightness, and slip over a predeclared dwell. If sensing
   is unavailable, an explicit current operator report must cover the missing
   condition; do not infer it from an API success or a stale close result.
6. **Reset.** Provide a measured, collision-checked supported placement procedure
   for the same horizontal grasp, or use an operator reset/handoff. Verify pitcher
   support before releasing and reobserve the empty gripper and new placement.

For each insertion clearance, combine localization, calibration, tool-offset,
orientation and tracking error conservatively in the relevant direction. Convert
angular error into finger-tip displacement using measured geometry. Require the
remaining clearance to exceed the team-approved margin. An unknown error term
blocks contact; a low mean calibration error does not remove it.

Do not reinterpret existing `cup` or `coffee` identifiers as a localized pitcher
without an agreed object contract. A new detection schema must be integrated
explicitly with the registry and shared target validation.

## Proposed trial protocol

No physical trial is admitted by this document alone. There is no taught side
grasp. The user subsequently authorized the complete 15-trial batch, but the live
pre-trial gate on 2026-09-19 blocked trial 1 before motion. Keep that authorization
and full unused budget for when the listed prerequisites are implemented and fresh
state is established; do not count camera-only observations as grasp attempts.

After the implementation and measurements exist, propose a maximum of **15 total
physical attempts**: up to 3 debug attempts (including at most 1 recovery), then
12 final trials. Carry accepted authorization through that finite budget without
asking again per attempt. A changed task scope or unknown state requires a new
admission decision. Camera observations and offline checks are separate from the
physical-grasp attempt count.

Before any trial, record numeric acceptance thresholds and their measurement
sources for approach horizontality, pose error, grasp force, test-lift height,
support clearance, slip, tilt, observation dwell, and safe return. Do not invent
these values from the photograph. Record the pitcher/finger geometry, load/fill,
support height, camera calibration, primitive revision and config snapshot.

Debug at one measured placement and heading, beginning with known contents/load
and confirmed empty gripper. Use observations to identify the failure before
changing one bounded parameter. Freeze the selected revision and grasp transform
before the final gate; positions and headings are perception inputs, not edits to
the recipe.

| Final condition | Placement | Handle heading | Repetitions |
| --- | --- | --- | --- |
| A1 | Measured central clear location | First reachable heading | 2 |
| A2 | Same central location | Distinct reachable heading | 2 |
| B1 | Distinct clear location along one table axis | First heading | 2 |
| B2 | Same second location | Second heading | 2 |
| C1 | Distinct clear location along the other axis | First heading | 2 |
| C2 | Same third location | Second heading | 2 |

Choose and record actual locations/headings within the reachable, visible region;
do not silently exclude a failed condition. Keep support height and load fixed in
this matrix. A changed tray, fill class, handle geometry, or camera setup requires
separate validation. If a condition informs a revision, all final results for the
new revision start anew within the remaining agreed budget.

A passing trial requires verified horizontal insertion, intended handle contact,
force/holding evidence, visible support separation, retention without excessive
slip/tilt for the declared dwell, and a supported reset for the next trial. Record
errors as failures and missing evidence as unknown. Only promote if all 12 final
trials of the frozen revision pass. This provides evidence for those tested
conditions, not a reliability guarantee across every point on the table.

## Required handoff artifacts

Keep raw images, configs, taught measurements and run logs under ignored `runs/`;
shared measured calibration belongs in a separate versioned calibration artifact
per repository policy. Save each candidate, numbered attempt, changed parameter,
gate decisions and failure/recovery evidence. Freeze executable phase plans in
`final/` only after an executable candidate exists. Write `validation.md` and
`findings.md`, then update the skill status and shared technique with sanitized,
evidence-backed findings. Never call this initial specification physically learned.
