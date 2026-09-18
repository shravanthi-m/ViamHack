# Barista Bot skill library

This is the agent's reusable task knowledge: primitive compositions, gates,
recovery recipes, bounded parameter choices, and evidence-backed techniques.
It follows the skill-refinement idea in [ASPIRE](https://github.com/NVlabs/ASPIRE)
and the agent/worker/findings separation in our local `aspire-repro` workspace.
It is a robot-task library, independent of any agent vendor or plugin format.

Start at [INDEX.md](INDEX.md). The agent reads before planning and writes after
reviewing evidence, following [the fix-loop runbook](../agents/fix_loop.md).

```text
skill_library/
  INDEX.md                      Task skills, status, and shared technique links
  techniques/                   Shared Markdown findings across tasks
  entries/<skill>/v1/skill.md    Versioned recipe, gates, scope, evidence, recovery
  entries/<skill>/v1/*.json     Runtime-compatible phase plans
  templates/                    Skill, review, findings, and validation templates
```

`primitives/` is the human-owned implementation layer. A library skill says how
to combine and check those tools for a task. The agent can refine compositions,
select supported bounded settings, and write generalized lessons. It cannot
manufacture a missing physical capability by changing a Markdown file.

## Status and evidence

- **Candidate:** draft or partially tested revision; usable for mock work and
  gated physical trials within an authorized scope, not a proven recipe.
- **Validated:** an unchanged revision met predeclared physical validation criteria
  for the recorded objects, station/config, and primitive versions.
- **Deprecated:** retained for history, with a reason and replacement if available.

Keep prior revisions. Changing a validated plan or using materially different
conditions creates a new candidate. Raw run evidence remains local under `runs/`;
commit sanitized findings and provenance references. An inaccessible source run
must be identified as such, never replaced by an invented confirmation.

Shared technique notes may include design contracts, negative findings, and tested
techniques, each explicitly labeled. The agent aggregates task findings directly;
a useful warning can be learned from a failed task without falsely promoting its
recipe. Avoid station-specific coordinates or secrets in general technique notes.

There are **no physically validated skills or learned robot techniques yet**.
The fixed-drink entry is a candidate scaffold, and initial technique notes are
contract guidance. Runtime calls do not automatically evaluate gates, run a repair
loop, or load this library; the operating agent performs those decisions.
