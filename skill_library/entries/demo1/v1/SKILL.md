---
name: demo1
description: Prepare Script 1: fixed-home coconut pour/return then pitcher pour/return using taught station tracks and explicit known-state gates.
---

# Demo 1 — v1

Status: **candidate; offline validated; no complete physical validation**.

Read [the operator runbook](../../../../docs/demo1_runbook.md) and
[taught source findings](../../taught-drink-demo/v2/SKILL.md). The requested order
is coconut pour/return → pitcher pour/return. Start home with an empty gripper;
cup stays fixed. Cup handling and shaking are separate future scripts. Spoon
handling is out of scope. Use the existing source recordings for Script 1.

Use the runbook's starting-state table before composing. Retrieve measured poses
from successful teaching episodes. Do not derive new coordinates from prose,
replace horizontal taught poses with tool-down
`go_to_pose`, or assume raw camera capture localizes anything.

Prepare exact episodes offline with `prepare-demo`; verify with `prepared-demo`.
Keep new recordings and revisions separate from a rehearsed pack. The existing
replay retains operator gates and has zero automatic recovery attempts. This
skill is a preparation/selection protocol, not an unattended physical executor.

Current evidence: on 2026-09-19, both existing compact **pose** tracks passed offline
replay validation (66 coconut and 84 pitcher waypoints). Existing live evidence
records coconut approach contact with the neighbor, a joint-replay pose mismatch,
then a planned lift/transport and a put-back command completion. No complete
two-source pour/return success is established. See `runs/live_demo_20260919/review.md`
and `runs/put_back/put_back/episode_20260919T163240_9b4ff176/result.json` locally.
Put-back reports gripper/pose feedback; it is not independent proof of task success.

Promotion requires the same frozen full script passing two consecutive observed
complete trials under the declared scene and fill levels. Home,
empty hand and a fixed cup narrow the task; arbitrary object poses or loaded-arm
recovery are not supported by this evidence.
