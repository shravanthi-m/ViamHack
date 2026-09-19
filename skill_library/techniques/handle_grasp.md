# Horizontal handle grasp

Status: source-inspected capability findings and an untested design; no successful
physical grasp evidence. Candidate: [pick-pitcher-handle v1](../entries/pick-pitcher-handle/v1/SKILL.md).

The public `go_to_pose` contract forces the tool downward. Translating at constant
height does not change that orientation; changing yaw cannot produce a horizontal
tool axis. `go_to_origin` reaches one fixed taught pose and supplies no transferable
handle grasp. These facts follow from `primitives/motion.py` and the registry,
reviewed for this task on 2026-09-19.

The overhead homography reports mean error 27.01 mm and maximum error 67.85 mm.
Its mapping is planar and does not measure elevated handle height. Sample error
statistics also do not certify an insertion-clearance uncertainty bound. No handle
contact target was established by the overhead observation.

Design hypothesis: reuse a measured handle-to-gripper transform with fresh 3D
handle localization, rotating both the tool offset and orientation for a new
handle heading. Validate actual horizontal insertion, finger clearance and lifted
retention before promoting this into a successful technique.

Local task evidence: `runs/fix_loop/pick-pitcher-handle/`; earlier scene observation:
`runs/overhead_observation_20260919/20260919T024726-e1fb6cc4/`. These ignored files
may not travel with the repository. Physical grasp attempts: zero. Current outcome:
candidate saved; execution blocked by missing capabilities, recorded at the user's
request. Recheck the catalog and measurements before future use.

Live pre-trial finding, 2026-09-19 02:56:25 UTC: overhead imagery can establish the
pitcher's visible 2D scene context, but the wrist camera at the current arm pose
looks away from the station. It cannot verify finger-to-handle alignment or grasp
contact. Camera capture completion is not grasp-success evidence. Before trials,
place a calibrated view where it can observe the fingers and handle during contact,
or provide another supported sensor contract. Trial 1 was gated out before motion;
the authorized 15-attempt budget remains unused.
