# Repository working agreement

Use the available web research and browsing tools when needed.

This is a hackathon integration scaffold. Humans own primitive implementations.
The agent owns task composition, task and phase gates, fault diagnosis/recovery,
and evidence-based refinement of reusable skills in `skill_library/`.
Keep robot algorithms in `primitives/`, plan execution in `runtime/`, and the older
attempts in `experiments/`. Do not wire experiments into the demo automatically.

Start with `agents/orchestrator.md` and `skill_library/INDEX.md`. For iterative
refinement, use `agents/fix_loop.md` and `agents/task_worker.md`. Use
`agents/task_gating.md` before execution and between task phases,
`agents/fault_recovery.md` after a fault, and `agents/skill_learning.md` when
reviewing outcomes and refining skills. For primitive work, read
`agents/primitive_author.md` and `primitives/README.md`; for experiments, read
`agents/trial_operator.md`. These are responsibilities of the active agent, not
automatically running background agents.

The human-facing entry point is a request to the coding agent. Follow the relevant
role instructions and invoke `python -m runtime` from the root on the user's behalf.
Default to offline validation/mock runs when preparing or testing a plan.
Never infer authorization for physical motion from a request to write a plan or
edit code. Use the physical execution path when the user explicitly requests a
robot run. Do not change machine configuration as part of language planning.
Carry existing run/trial authorization through its agreed recovery and refinement
budget; do not request it again for each in-scope attempt. New physical trials are
not authorized by a documentation or planning request. Unknown state blocks motion
until it is observed or reset, even when a retry budget remains.

The agent may write and version skill compositions, task gates, recovery recipes,
and evidence-backed settings within the team's exposed parameter ranges. Humans
own primitive internals, calibration, and hardware limits. Never broaden those
limits to make a skill pass. Keep candidate and validated skills distinct;
mock completion and successful API calls are not physical success evidence.

Preserve the explicit registry and shared target contract. Reject unsupported
tasks rather than invent tools, coordinates, or unimplemented capabilities. Keep
credentials, local calibration, and run evidence out of Git. After integration
changes, run `python -m unittest discover -s tests -v` with requirements installed.
