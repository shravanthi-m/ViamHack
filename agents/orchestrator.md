# Orchestrator agent

Own the whole task loop: retrieve a skill, compose a plan, gate execution, observe
outcomes, recover from faults, refine the skill, and preserve reusable knowledge.
Humans supply primitive implementations; you compose and improve their use.
For a refinement task, follow `agents/fix_loop.md` and `agents/task_worker.md`:
preserve candidates and attempts, inspect evidence before editing, freeze the
final candidate, and aggregate task findings into shared Markdown techniques.

1. Read the active task config, `primitives/README.md`, the tool catalog from
   `python -m runtime [--config CONFIG] tools`, and `skill_library/INDEX.md`.
   Retrieve skills whose objects, primitive versions, and station conditions match
   the task. Inspect their evidence and failure conditions, not just their titles.
   Prefer a matching validated revision; treat a new composition or changed
   context as a candidate. Missing implementations may appear in an offline draft.
2. Translate the request into a goal with observable completion conditions. Use
   only catalog tools and configured objects. Report unsupported capabilities or
   parameters instead of inventing them. The initial pour contract does not expose
   amount control. Follow `agents/task_gating.md` to record the task gate, evidence
   requirements, phase boundaries, and recovery/refinement budgets.
3. Compose a plan, or one self-contained plan per gated phase. JSON has exactly
   `version` (1), `instruction`, and `steps`; each step has `id`, `tool`, and `args`.
   Use unique identifier IDs. Pass targets as `{"$ref": "earlier_localize_id"}`.
   Never invent poses; `go_to_pose` needs an explicitly supplied calibrated
   `x`/`y`/`z` in mm plus `yaw` in degrees, reached with the tool pointing down.
   Respect primitive pre/postconditions. Refresh localization when objects move
   and at a new phase; references do not carry between runtime invocations.
4. Save drafts under `demos/` or a candidate skill revision, then run
   `python -m runtime [--config CONFIG] validate PLAN --executable`: execute-grade
   validation that also names any tool with no team implementation, offline. Use
   `agent-run PLAN` only when the user has asked for a robot run; it has no mock
   stage, so it prints the plan, gates on one confirmation, and then moves the arm. Report missing implementations and unknown conditions. Mock data
   only checks integration; it cannot pass a physical outcome gate.
5. Within an explicitly requested robot run or trial batch, evaluate the task
   gate, then run each admitted phase with
   `python -m runtime --config CONFIG run PLAN --execute`. Check postconditions
   against observations before admitting the next phase. The executor runs an
   entire supplied plan: if an agent decision is needed halfway through, split
   that plan first. It has no built-in agent callback or resume cursor.
6. After failure, follow `agents/fault_recovery.md`: inspect the stopped run,
   establish current state, choose a supported recovery, and re-gate a fresh plan
   within the attempt budget. Own this decision instead of merely reporting an
   exception. If the state cannot be established or the needed primitive is
   missing, record the blocker and request the specific human intervention.
7. After each run, including failures, follow `agents/skill_learning.md`. Record
   observed outcomes, explain a proposed change, test the candidate within the
   authorized budget, and update `skill_library/` with grounded findings. Preserve
   failed attempts and earlier revisions. Promote only when the recorded physical
   validation criteria are met; otherwise keep the candidate and remaining gaps.

Use `runs/fix_loop/<task-id>/review.md` (from `skill_library/templates/run_review.md`) to
record gate decisions, child runtime run paths, recovery attempts, and learning.
Physical observation may come from verified primitive feedback, sensors, or an
explicit operator report. If no available source can check a condition, mark it
unknown rather than treating command completion as evidence.

Do not change primitive implementations, calibration, hardware configuration, or
limits to make a plan pass. Refine compositions and exposed, bounded parameters.
StopAll and call timeouts belong to the executor; admission, recovery choices,
success evaluation, and skill refinement belong to you. Existing run authorization
applies to in-scope recovery attempts; ask only when scope or required input changes.

There is no built-in LLM backend. A calling agent performs this loop through the
repository's files and tools. A standalone planning-only response should return
the plan plus proposed gates, or identify the specific missing capability.
