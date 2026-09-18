# Trial operator

For record/replay use `experiments/record_replay/README.md`. For ASPIRE-inspired
supervised refinement use `experiments/skill_learning/README.md` and that folder's
profiles, station config, and lessons. The demo executor does not invoke either.

Use the appropriate module CLI from the repository root. Keep offline checks and
physical trial runs explicit. After a physical trial, record the observed outcome
and evidence path; command completion is not task success. Change one experimental
parameter at a time and preserve failed evidence. Follow `agents/skill_learning.md`
to aggregate applicable findings into candidate entries in `skill_library/`, with
links to original runs and explicit context differences. The agent owns refinement
and evidence-based promotion of those skill compositions. Request primitive
changes from their human authors; do not silently change machine configuration,
primitive internals, or the registry to fit an experiment.
