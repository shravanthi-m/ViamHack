# Recovery

Status: design contract only; no validated recovery recipe yet.

First read the aborted run and establish fresh physical state. Do not release an
unknown held object or repeat a pour just because a call raised an error. Use only
available recovery tools, within the task budget. A verified human reset is a
supported way to recover when no primitive can handle the current state.

## Evidence-backed findings

On 2026-09-19, the signature routine grasped the coconut carton, passed its grasp
confirmation, and entered lift, transport, and pour phases. During pour, Viam
rejected motion because all IK solutions violated the self-collision constraint
between `arm:wrist_link` and `cam_origin`. StopAll returned; the run remains failed.
Evidence: `runs/claudia/67f51a84b4794dc5abc2334a5a121999/demo/episode_20260919T183957_27693de1/`.

This is a planner-predicted collision, not evidence that physical contact occurred.
The log has phase entries but no per-waypoint completion cursor, and poured volume
is unknown. Do not blindly continue or repeat the pour. Preserve collision limits;
the primitive/calibration owner must review the taught path and geometry before a
new pour attempt. Fresh stopped/held state and taught return grip/orientation are
required for a separately gated Reset. No recovery trial or successful resume is
established by this incident.


The subsequent Reset attempt in
`runs/claudia/72906110c51e467c99992a74ae665c97/reset/episode_20260919T184231_b855cd65/`
failed with `xArm: Emergency Stop Button Pushed In`. This is not a successful
recovery. Hardware readiness must be restored by an operator after inspection;
software must not release/bypass the E-stop or auto-resume the interrupted pour.
