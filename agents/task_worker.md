# Task worker

Own one task directory under `runs/fix_loop/<task>/`. Read the primitive contract,
the selected skill, shared technique notes, and `agents/fix_loop.md` before editing.
The coordinator may be the same agent working in a different phase of the loop.

1. Write candidate `001` as one or more runtime-compatible plan JSON files. Record
   the hypothesis and exact configuration/primitive revision. Use only documented
   tools. A task program here is a composition of human-built primitives, not
   generated driver code.
2. Validate and mock the candidate. Then run permitted physical debug conditions
   through `agents/task_gating.md`, recording a fresh numbered attempt for each.
3. Inspect every attempt's plan/config, events, result, returned observations, and
   referenced sensor/image evidence. Separate facts from hypotheses and command
   completion from task success. Diagnose before editing.
4. Apply `agents/fault_recovery.md` where needed. A recovery attempt counts against
   its budget and is preserved even if it makes the overall task succeed.
5. Change one composition choice or exposed bounded parameter in candidate `002`,
   then repeat as evidence warrants. Stop on exhausted budget or missing physical
   capability. Do not widen limits or edit team primitive internals.
6. Select and freeze a candidate in `final/`. Run it unchanged on the declared
   final-gate conditions within the remaining physical-trial budget. Record all
   outcomes using the validation template; unknown is not a passing result.
7. Write `findings.md` with the candidate, per-condition results, evidence-backed
   causes, changes, reusable patterns, and unresolved concerns. The coordinator
   uses it to update the shared library. A task can produce useful findings even
   when no candidate passes.

Never fabricate observations, count mock trials as physical evidence, or inspect
held-out evaluation results while tuning when the task defines a held-out split.
