# Skill learning and refinement

Accumulate reusable sensorimotor task knowledge in `skill_library/`. Here a skill
is a versioned primitive composition, bounded settings, entry/exit gates, recovery
guidance, and evidence describing where it works. Humans continue to own primitive
internals. The agent owns the observe → diagnose → revise → trial → evaluate loop.
Use `agents/fix_loop.md` for the task/attempt layout and coordinator aggregation.
Write task findings before updating shared `skill_library/techniques/` notes.

1. Retrieve a matching library entry before composing from scratch. Check its
   validation context and limitations. A validated skill in a different station,
   object geometry, or primitive revision is a candidate in the new context.
2. Label the task outcome using observations: success, failure, or unknown, with
   the relevant failure detail. Keep executor completion separate. Preserve the
   original run evidence; add interpretation in the task review.
3. State one hypothesis grounded in a failed/successful observation. Change one
   composition choice or one exposed parameter at a time. Copy the previous skill
   into a new candidate revision and record the diff and expected effect. Tune
   only parameters the team's primitives expose within their declared bounds.
   No arbitrary torque changes or inferred mass/volume. Missing interfaces become
   requests to primitive authors, not agent-generated hardware code.
4. Choose observable acceptance criteria, repeat count, test contexts, and a
   finite trial budget **before** testing. Run offline checks, then gated physical
   trials within the authorized scope. Reset the scene as required between trials;
   retain successes, failures, unknown outcomes, and recovery attempts.
5. Compare outcomes for the same context and exact revision. If the predeclared
   criteria pass, mark that revision validated for the tested conditions and update
   `INDEX.md`. If they do not, retain it as candidate or mark it deprecated with a
   reason. Keep the previous validated version available for rollback. Broader
   claims require separate trials in the broader contexts.
6. Aggregate the useful lesson into the entry: when to use it, when not to, which
   gate detects the failure, and which recovery was actually observed to work.
   Record source run paths and config/primitive revisions. Keep private images and
   calibration in ignored run data; version a sanitized evidence summary in the
   library. If evidence is unavailable to the next operator, mark that limitation.

Use `skill_library/templates/skill.md` for a new revision and
`skill_library/templates/run_review.md` for evidence. Never backfill success
counts from mock runs or lower acceptance thresholds after seeing failures.
Library revisions are written by the agent; a status label never bypasses current
implementation readiness, task gates, or calibration checks.

The earlier `experiments/skill_learning/` harness is an optional source of trials
and lessons. Cite its original run IDs and translate findings only where its
objects, station, and contracts match. Do not automatically promote its profiles
into the demo or conflate its config schema with the demo's schema.
