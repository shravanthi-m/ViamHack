# Coffee, coconut water, and stir — v1

Status: **candidate**. This is an integration recipe, with zero physical trials.
Required implementations and observation contracts are still supplied by the team.

Goal: coffee and coconut water poured into the cup, stirred for five seconds,
sources returned, and spoon returned/released with the cup stable. No amount claim.
Objects: `coffee`, `coconut_water`, `cup`, `spoon`. Use the active calibrated config
and record its snapshot plus primitive revision in the task review.

## Phases

| Plan | Entry conditions | Exit gate |
| --- | --- | --- |
| [01_coffee.json](01_coffee.json) | Empty gripper; source/cup ready; current scene confirmed | Coffee pour outcome observed; source upright and returned; empty gripper; cup stable |
| [02_coconut.json](02_coconut.json) | Coffee gate passed; scene still suitable for next pour | Coconut pour outcome observed; source upright and returned; empty gripper; cup stable |
| [03_stir.json](03_stir.json) | Pour gates passed; spoon/cup ready; empty gripper | Stir outcome observed; spoon returned/released; cup stable |

Each phase includes fresh localization and resolves only its own references.
The agent invokes one phase, evaluates its gate, then decides whether to continue.
The existing `demos/fixed.json` remains a whole-sequence mock/integration example.

## Recovery and learning

Use the shared recovery technique and task runbook. No robot recovery recipe has
been validated. Diagnose partial effects before planning a retry, especially a
partially completed pour. If held state or cup contents cannot be established,
require an observation or verified scene reset. Choose bounded attempt/trial
budgets during task preparation; do not infer unlimited retries from this entry.

The five-second stir is a demo choice, not a learned optimum. Changing duration
within the team's allowed range creates a candidate; parameters internal to pour
are not exposed to this plan. Before physical validation, specify observation
sources, repeat counts, and acceptance criteria. Freeze and test the same revision
across those conditions and retain all results.

## Evidence and limitations

- Physical successes / failures / unknowns: 0 / 0 / 0; untested.
- No measured volume, force, or material-generalization claim.
- Mock phase checks establish only plan/interface compatibility.
- Team feedback or explicit observations are needed to evaluate physical gates.
- No previous validated revision exists.
