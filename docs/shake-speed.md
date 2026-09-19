# Standalone shake speed diagnosis

Applies to the root `shake_full.py`. The runtime primitive in
`primitives/shake.py` and the older `primitives/shake_full.py` are separate
implementations and are not changed by this fix.

## What was slowing the loop

The root script already requested 90 degrees/second with `set_speed`. Its
30 degrees/second limit in `primitives/motion.py` was therefore not the cap on
the oscillation. `motion.plan_to` also does not reapply that local speed setting.

Each half-stroke contained four awaited `move_to_joint_positions` calls. Each
call used the driver's default interpolation and completion wait. The script
then slept for the full nominal sample interval, adding motion and network time
on top of the requested period. Changing `frequency_hz` only shortened those
sleeps; it did not make the four moves complete any faster.

## Direct controller path

The upstream UFactory driver accepts this per-call extension:

```python
await arm.move_to_joint_positions(
    JointPositions(values=endpoint_joints),
    extra={"direct": True, "speed_d": 90.0, "waitAtEnd": True},
    timeout=timeout,
)
```

In the inspected driver, `direct` selects mode 0 and `P2PJoint`, skipping the
built-in interpolation when no external trajectory generator is configured.
The driver clamps movement speed to 3–180 degrees/second and acceleration to
its supported range. This patch does not raise those limits, change collision
sensitivity, or override acceleration. The old persistent `set_speed` mutation
is replaced by a speed request on stroke calls only.

Sources: [move options and clamping](https://github.com/viam-modules/viam-ufactory-xarm/blob/d83b4d97988a6e7716329b512010597d68516c0f/arm/xarm.go#L656),
[direct motion and completion handling](https://github.com/viam-modules/viam-ufactory-xarm/blob/d83b4d97988a6e7716329b512010597d68516c0f/arm/comm.go#L503).
Inspected 2026-09-19; the deployed module version has not been verified.
The installed Python SDK 0.80.0 accepts `extra` on joint moves; it does not expose
`Arm.move_through_joint_positions`.

## Changed behavior

Direct mode uses one awaited endpoint move per half-stroke, then checks joint
readback before reversing. Both modes use the same captured endpoints and joint
line. Timing includes command and readback latency; it sleeps only for the
remaining interval. Late strokes do not trigger queued catch-up moves. A move
already in flight can finish after the requested duration, bounded by its RPC
timeout; no additional stroke starts after the duration expires.

Results include achieved frequency, move count, command/readback time, pacing
sleep, and overrun count. `frequency_hz: 20` remains the existing requested
ceiling, not a demonstrated physical rate. Short moves can be acceleration-bound
before reaching the requested joint speed.

For an offline code comparison, `control_mode: "sampled"` retains the previous
waypoint geometry with corrected pacing; `samples_per_stroke` affects only that
mode. These settings belong to this standalone script, not the runtime primitive.

## Validation status

Candidate, tested offline only. Tests cover timing with simulated command and
readback latency, endpoint alternation, late commands, invalid feedback, failure
propagation, and StopAll/connection cleanup. No machine setting was changed and
no robot was moved during development. Driver support and achieved physical rate
still need a separately authorized trial.

As before, capturing collision-planned endpoints does not validate the entire
direct joint path against obstacles. Full path clearance, held-object stability,
and the appropriate speed for the actual payload remain physical trial gates.
