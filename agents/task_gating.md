# Task and phase gating

Decide whether the next task or phase may run. Use the active configuration and
the chosen skill revision. Record each decision in the task's run review with
evidence, not just a checklist of assumptions.

| Gate | Evidence to check |
| --- | --- |
| Task admission | Requested goal and run mode; implemented tools; matching skill context; calibrated config; explicit physical-run scope; bounded attempts |
| Phase entry | Current object locations, held-object state, required resources, and the primitive's entry conditions |
| Phase exit | Actual observable outcome and resulting state, including what the next phase requires |
| Recovery admission | Stop outcome, fresh state, known fault class, supported recovery tools, remaining attempts |
| Skill promotion | Repeated observed physical outcomes for the exact revision and context, including failures |

Use `pass`, `blocked`, or `unknown`. `unknown` does not admit physical execution.
Record observation source/time and why it is still applicable. Freshness depends
on scene changes: an earlier pose is not current just because its JSON is valid.
Sensor feedback and explicit operator observations are acceptable when they
actually establish the condition. If an observation tool is missing, identify it
for the team; do not pretend the runtime provides it.

Set a finite recovery-attempt budget per task and refinement-trial budget per
candidate before physical execution. Use an existing user/team budget when given;
otherwise propose one as part of preparation. Do not silently reset counters after
a fault, new plan filename, or failed candidate. Stay within existing authorization.

For the drink demo, useful gates are after coffee pouring, after coconut-water
pouring, and after spoon return. Require evidence for source return/empty hand,
cup stability, and the requested pour outcome before the next phase. Amount is
unknown unless measured; no automatic inference from command completion. Keep
pick/insert/stir/return together unless the team defines an observable, supported
intermediate state in which the robot can wait.

The runtime invokes every step in one supplied plan serially. Agent-owned gates
therefore sit **between separate phase runs**, with fresh localization inside
each plan. See `skill_library/entries/fixed_drink/v1/`. A monolithic fixed plan is
still useful for mock integration, but does not gain intermediate agent gates by
having a skill document next to it. Primitive authors own immediate checks inside
an operation; the agent cannot interrupt a pour to evaluate a gate mid-call.
