# Evidence-backed skill memory

After trials, store one short Markdown note per reusable finding here:

- Object/material, measured total mass, fill level, gripper model/module version.
- Hypothesis and the single parameter changed (force, speed, pose, or dwell).
- Exact trial paths and profile/config IDs; success count / total attempts.
- Failure conditions (slip, crush, spill) and where the rule should not apply.
- A separate set of repeat trials at new masses/materials before claiming transfer.

No learned lessons yet. A completed API sequence is not a successful pickup/pour.
Keep station coordinates in station.json and tuned parameters in profiles/.
Start with rigid empty objects, then known added masses, then soft cartons.
Try small force increases for slip only when force control is supported; reduce
force for crushing. Change one variable per batch and retain failed evidence.
Do not infer weight or stiffness from RGB-D alone. A scale and observed deformation
are the minimal ground truth; a Viam force/torque sensor can be added later.
