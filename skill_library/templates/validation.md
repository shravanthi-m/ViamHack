# Final gate — <task / candidate>

Define conditions and acceptance criteria before testing. Freeze the same plans,
config, and primitive revision for every final-gate run. Edits restart the gate
for a new candidate and consume the existing budget.

- Frozen plan paths and revision/hash:
- Config snapshot and primitive revision:
- Predeclared conditions, repeats, success criteria, and budget:
- Outcome observation method:

| Condition / repeat | Run path | Executor status | Observed success / failure / unknown | Evidence and reason |
| --- | --- | --- | --- | --- |

- Physical success / failure / unknown counts:
- All declared trials present:
- Criteria met: yes / no / unknown
- Tested scope, remaining limitations, and resulting skill status:

Errors count as failures. Unknown outcomes do not count as successes. Mock runs
cannot satisfy this physical gate. Keep optional held-out evaluation separate.
