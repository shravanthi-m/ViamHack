# Pouring

Status: design contract only; no physical findings yet.

The current `pour` contract starts and ends empty-handed and returns the source.
Gate the next phase on observed source return, cup stability, and pour outcome.
The interface does not expose amount control. Repeating a failed call could add
more liquid after a partially successful pour; establish state first.

## Evidence-backed findings

None yet. Record material/geometry, measured fill conditions, config/primitive
revision, run evidence, and where a learned technique failed as well as worked.
