# Task policy and Viam machine configuration

Keep two sources of configuration with distinct responsibilities:

| Setting | Source |
| --- | --- |
| Hardware models, drivers, connection attributes, simulation/fake model selection | Viam machine config |
| Frame tree, TCP transforms, configured collision geometry | Viam machine config |
| Torque/force/speed capabilities and hardware defaults | Installed component/module and its Viam settings |
| Credentials/address | Local `.env` / environment variables |
| Task roles → resource names, working frame | `config/demo.json` or full `config/local.json` |
| Allowed objects, step/time limits, maximum stir duration | Local task config |
| Measured task workspace and calibration declaration | Local task config |
| Object-specific grasp/pour tuning | Team-owned config consumed/validated by its primitive |

Viam already stores component model, attributes, and frame information in its
[machine configuration](https://docs.viam.com/hardware/machine-configuration/).
The demo should reference that hardware rather than reproduce its driver settings.

`python -m runtime inspect` connects read-only and prints `resource_names` and
`get_frame_system_config()` results. This lets you copy the correct bindings and
see configured frames/geometry. It does not edit settings, capture camera images,
probe torque extensions, or automatically choose between multiple arms/cameras.
These runtime APIs are documented in Viam's
[machine management API](https://docs.viam.com/reference/apis/robot/).

Those APIs do **not** provide a universal torque/simulation settings object. For
arbitrary component attributes, inspect/export the machine's CONFIGURE JSON or use
the appropriate Viam app API with suitable permissions. Model-specific force
readback belongs in a verified primitive adapter. Seeing a torque control in a UI
does not imply that every gripper implements the same SDK command or force units.

The task workspace is a separate, measured policy: frame geometry alone does not
tell the agent which area a team has approved for this demo. Leave
`calibrated=false` and bounds null until measured. The executor refuses physical
runs in that state. With `--execute`, it checks supplied poses and localization
results against configured bounds. It cannot inspect internal primitive paths;
their authors must enforce those bounds and use Viam's collision planning too.

Use `cp config/demo.json config/local.json`, then edit that full file. Select it
explicitly using `python -m runtime --config config/local.json ...`. There is no
hidden merge, automatic calibration, or automatic retrieval of hardware settings.
The example's resource names come from the earlier station config and should be
verified with `inspect` on your machine. `--execute` invokes the configured Viam
machine, which may itself use a simulated model; offline mock mode never connects
to either real or simulated hardware.

The earlier learning experiment keeps its **own** station/profile schema under
`experiments/skill_learning/`. Do not feed it the new demo config or vice versa.
