# Recovery

Status: design contract only; no validated recovery recipe yet.

First read the aborted run and establish fresh physical state. Do not release an
unknown held object or repeat a pour just because a call raised an error. Use only
available recovery tools, within the task budget. A verified human reset is a
supported way to recover when no primitive can handle the current state.

## Evidence-backed findings

None yet. Record the fault and entry state, attempted remedy, actual observations,
partial effects, budget consumed, and conditions where the remedy must not be used.
