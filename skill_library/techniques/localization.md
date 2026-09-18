# Localization

Status: design contract only; no physical findings yet.

`localize` returns a target in the configured task frame. Plans reference the
returned target; they do not invent coordinates. Re-localize after scene changes
and at new phase boundaries. A valid pose shape does not establish correct object
identification; use the team's observation/confidence contract for admission.

## Evidence-backed findings

None yet. Append findings with source task/revision, observation evidence, tested
conditions, failures, and limits. Do not copy simulator-specific heuristics here.
