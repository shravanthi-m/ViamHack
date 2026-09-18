# Primitive author

Work in your assigned module under `primitives/`; read its README and shared types.
Keep the public async signatures stable so teammates can integrate independently.
Read resource names and frame from `ctx.config`; obtain SDK handles from
`ctx.robot`. Keep SDK imports inside your implementation if offline tooling should
remain usable without the SDK.

Implement the documented pre/postconditions, validate object-specific parameters,
and check SDK return values. Raise on failure or uncertainty. Return a JSON object
on completion. Propagate cancellation, use awaited SDK calls, and avoid blocking
the event loop. Do not swallow errors or return a fake success. Honor local task
limits in addition to the Viam machine's configuration; validate intermediate
poses generated inside your primitive because the executor only sees endpoints.

Expose the observations needed by task gates: what was measured, resulting held
object/state, and evidence references where available. Document what your return
value proves and what remains unknown. The agent owns task-level gates, recovery
selection, and skill learning; supply supported recovery operations and bounded
parameters rather than requiring it to guess at motion or driver settings.

Enable each ready function in your module's `IMPLEMENTED` set. Include meaningful
fake-adapter contract tests, then give the integration team the implementation
names, calibration requirements, observed pre/postconditions, and any limitations.
Leave teammate modules and the orchestrator untouched unless a shared contract
change is agreed. An existing recording can be reused only through an explicit,
reviewed adapter with the same contract.
