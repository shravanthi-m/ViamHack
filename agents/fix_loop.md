# Agent-operated fix loop

Use this runbook for a request to improve a task or learn a reusable skill. It
adapts the local `aspire-repro` separation of coordinator, task worker,
deterministic replay, findings, and shared Markdown skills. The same active agent
can perform the roles sequentially. There is no required agent provider or
automatic dispatcher, and only one physical run may own the robot at a time.

## Coordinator

1. Read `skill_library/INDEX.md`, relevant `skill_library/techniques/` notes, and
   existing task findings. Select the pending task and matching candidate/revision.
2. Establish the goal, permitted changes, debug conditions, attempt budget, final
   gate criteria, and physical-run scope. Record these in the task's review before
   starting. Debug conditions are reproducible physical setups, not simulated seed
   numbers. Examples include known source placement and measured starting fill.
3. Follow `agents/task_worker.md` for that task. If work is delegated, each worker
   owns only its task directory; the coordinator is the single writer of shared
   library notes. Coordinate all physical access serially.
4. Read the completed `findings.md` and final-gate results. Aggregate grounded
   lessons into the appropriate shared Markdown technique and index. Updating
   those files is how the agent accepts knowledge into the library; no separate
   plugin installation or human editorial approval is required. Preserve evidence
   scope and distinguish a useful negative finding from a validated recipe.
5. Mark the task `validated`, `needs_work`, or `blocked` with the reason and remaining
   budget. Revisit it only within the authorized scope and budget. Keep failed
   findings available to future tasks instead of repeatedly rediscovering them.

## Task artifacts

```text
runs/fix_loop/<task>/
  review.md                   Scope, conditions, budgets, gates, observations
  candidates/001/             Initial phase plans and configuration snapshot
  candidates/002/             Revised plans; earlier versions remain intact
  attempts/attempt-001.md      Candidate, condition, runtime run paths, outcome
  attempts/attempt-002.md      Next attempt, including faults and recovery
  final/                      Unchanged plans used for the final gate
  validation.md               Per-condition final results, criteria, conclusion
  findings.md                 Root causes, fixes, reusable lessons, open issues
```

Raw executor evidence stays in its generated `runs/<run-id>/` directory, linked
from the attempt record. The task directory aggregates it without rewriting it.
Use `skill_library/templates/` for reviews, findings, and final validation.
These agent-written artifacts are a workflow protocol, not a background service.

The runtime only executes an existing plan. It does not invent a repair, retry a
task, interpret a physical outcome, or update shared skills. Check meaningful
returned observations and any referenced image/sensor evidence after each attempt
before editing the next candidate. If the team has not exposed enough feedback,
record that gap instead of claiming an observed root cause.

Freeze the selected candidate and evaluate the **same** plans on every declared
final-gate condition. Count errors as failures and missing observations as unknown.
Do not collect one good result from each different candidate and call that a pass.
Optional transfer/held-out conditions stay separate from debug evidence; if their
results inform a repair, they are no longer held out for that revision. This repo
does not contain a simulator oracle or software-enforced test partition.
