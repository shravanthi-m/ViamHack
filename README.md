# Varista

Meet **Claudia**, Varista’s robot bartender. She pours and prepares drinks using
Viam and human-taught routines, composed and refined by an agent.

For the audience display, run `.venv/bin/python -m runtime --config config/local.json observer` and open
http://127.0.0.1:8765. The overhead camera streams automatically. The [observer guide](docs/observer.md) covers the front page
and open counter: a camera scene, Pour signature drink / Reset / Shake buttons,
and one custom-task input. Preview is the default; opt-in supervised execution
runs the frozen coconut-water → pitcher routine with existing operator gates.
The [fixed-task scripts](docs/fixed_tasks.md) also provide supervised Reset for an
identified carton/pitcher or empty hand, and a three-second Shake for an already
held object. Shaker finding/pickup/return and arbitrary custom tasks are unavailable.

For supervised hand-guided demonstrations and bounded grasp adaptation, see
[Teach and replay](docs/teach_replay.md), including the coconut-water and pitcher commands.

## Table of contents

- [Get started with an agent](#get-started-with-an-agent)
- [Demo goals](#demo-goals)
- [Team workflow](#team-workflow)
- [Agent learning loop and skill library](#agent-learning-loop-and-skill-library)
- [Repository structure](#repository-structure)
- [Machine setup and configuration](#machine-setup-and-configuration)
- [Developer command reference](#developer-command-reference)
- [Earlier experiments](#earlier-experiments)
- [Status and next steps](#status-and-next-steps)

## Get started with an agent

Open this repository in your coding agent with file and terminal access. Start by
asking the agent to read [AGENTS.md](AGENTS.md) and follow
[agents/orchestrator.md](agents/orchestrator.md). You describe the task; the agent
retrieves relevant skills, prepares a plan, gates its execution, and uses outcomes
to recover and improve reusable skills.

### Try the fixed demo

Give the agent this prompt:

> Read AGENTS.md and agents/orchestrator.md. Prepare the fixed Barista Bot demo
> using skill_library/entries/fixed_drink/v1/skill.md. Check implementation
> readiness, validate and mock its phase plans, and describe the evidence needed
> at each gate. Show me missing implementations and run results.

This works before the team finishes the primitives. The mock uses fabricated
observations to check the handoffs; it does not connect to a robot or simulate
physical motion.

### Give it a language instruction

For example:

> Follow agents/orchestrator.md. Pour coconut water into the cup, skip the coffee,
> and stir for five seconds. Save the plan under demos/, validate it, and test it
> in mock mode. Tell me which implementations are still needed.

The agent composes only the tools in the primitive catalog. It localizes objects
before using them and reports unsupported requests instead of inventing a tool or
coordinate. Fixed and language-directed tasks use the same executor.

Your coding agent is the planner. There is no separate LLM service to start, and
you do not need to launch a Python program yourself. The agent calls the runtime
through its terminal tools; the commands are documented later for developers.

### Run on the robot when ready

After the team implements the required primitives and completes
[machine setup](#machine-setup-and-configuration), explicitly ask the agent:

> Run the fixed drink skill on the configured Viam machine using config/local.json.
> Check its task and phase gates. Allow at most one supported recovery attempt;
> if the observed state cannot be recovered with available tools, report what
> human reset is needed. Record outcomes and reusable findings in the skill library.

A request to prepare or mock a task does not command the robot. For an explicitly
requested robot run, the agent uses the physical execution path. Missing
implementations or calibration block execution. Plans, configuration, step logs,
and results are saved under `runs/`.

## Demo goals

1. **Fixed task:** localize the cup and coffee, pour coffee into the cup, localize
   coconut water and pour it into the cup, then pick up the spoon, insert it,
   stir, and return it.
2. **Language-directed task:** compose the team's primitives into a sequence based
   on a human instruction, such as leaving out coffee or changing stir duration.

Start with a reliable pour between fixed, calibrated positions. Build up to
localization and the full sequence incrementally. Amount control, froth checks,
coffee-machine buttons, and espresso/americano/milk-coffee presets remain later
extensions; they are not capabilities of the current scaffold.

## Team workflow

Humans own the primitive implementations. The agent owns task composition, gates,
fault recovery, and skill refinement. The runtime executes each supplied phase
serially and handles result references, timeouts, stopping, and logs.

| Work area | Team handoff |
| --- | --- |
| Motion and pouring | Implement `go_to_pose` and a reliable pick → pour → return/release cycle; specialize pouring for coffee and coconut water |
| Perception | Implement `localize`, returning an object target in the configured frame; the measured overhead pixel-to-millimetre transform is already wired, so detection is what is left; develop against saved observations while the arm is occupied |
| Spoon and integration | Implement pick, insert, stir, and return; coordinate configuration and end-to-end handoffs |

Start with [primitives/README.md](primitives/README.md) and
[agents/primitive_author.md](agents/primitive_author.md). Each team can work in its
own module against the shared signatures. Enable a ready function by adding its
name to that module's `IMPLEMENTED` set. Existing tools need no orchestrator edits;
a new tool also needs a registry entry and an agreed contract.

Mock runs let the team check integration in parallel. Reliable physical pouring
is still the first milestone: amount control and feedback loops need actual pour
behavior and observations to validate. Coordinate access to the arm for physical
trials.

## Agent learning loop and skill library

The agent operates a fix loop like the local `aspire-repro`: prepare a candidate,
run it through a deterministic worker, inspect evidence, diagnose failures, revise,
and evaluate an unchanged final candidate. Reusable findings accumulate as shared
Markdown skills. This follows the skill-refinement approach described by
[ASPIRE](https://github.com/NVlabs/ASPIRE); no simulator-specific APIs or seed rules
are imported into the robot workflow.

```text
Retrieve skill → compose → gate → execute phase → observe
                            ↑                       ↓
                      recover / revise ← diagnose outcome
                                                    ↓
                         final validation → findings → skill library
```

The [fix-loop runbook](agents/fix_loop.md) defines the coordinator and task-worker
roles, candidate/attempt artifacts, final validation gate, and aggregation of
findings. One active agent can perform both roles. Physical access remains serial.
Use this prompt to start preparation:

> Follow agents/fix_loop.md for the fixed drink task. Read the skill library,
> prepare a candidate and its observable gates, and define a bounded trial plan.
> Run offline checks now. After authorized physical trials, diagnose outcomes,
> revise within the budget, and aggregate findings into reusable skills.

The agent owns these decisions:

| Responsibility | What the agent does |
| --- | --- |
| Task and phase gating | Checks readiness and observed entry/exit conditions; records pass, blocked, or unknown |
| Fault recovery | Reads failed-run evidence, establishes current state, selects a supported bounded recovery, and re-gates the remaining task |
| Skill refinement | Changes compositions or exposed bounded settings, tests a hypothesis, and compares actual outcomes |
| Knowledge aggregation | Updates shared techniques and versioned recipes with evidence, failure conditions, and limits |

[skill_library/](skill_library/README.md) holds the shared techniques and task
recipes. Its [index](skill_library/INDEX.md) distinguishes candidates from validated
revisions. Current entries are scaffolds with no physical validation; successful
mock runs cannot promote them. Useful negative findings are retained too.

Gates currently run **between separate phase invocations**, not as hidden callbacks
inside Python. The fixed skill includes coffee, coconut, and spoon phase plans.
Each gets fresh localizations; the agent checks its outcome before admitting the
next. After a fault, the runtime stops and the agent decides the recovery. This
agent workflow is the learning loop; the runtime does not train, retry, or rewrite
skills itself. When feedback is missing, the agent records that gap and obtains
the needed observation or human reset.

## Repository structure

```text
agents/                         Role instructions for the coding agent
skill_library/                  Agent-maintained recipes, gates, recovery, findings
  INDEX.md                      Candidate/validated skills and shared techniques
  entries/                      Versioned task skills and phase plans
  techniques/                   Reusable Markdown knowledge across tasks
  templates/                    Review, findings, and validation artifacts
primitives/                     Human-owned tool implementations and contracts
  camera.py                     Photograph a configured camera view
  localization.py               Locate objects in the task frame (pixel -> mm)
  motion.py                     Go to a pose
  pouring.py                    Pick, pour, return, and release
  spoon.py                      Pick, insert, stir, and return
  types.py / registry.py        Shared types and available-tool catalog
runtime/                        Plan validation, execution, connection, and logs
demos/                          Fixed and language-authored plans
config/                         Task settings and local calibration
docs/                           Configuration reference
experiments/
  record_replay/                Earlier teaching, replay, and smoothing tools
  skill_learning/               Earlier ASPIRE-inspired supervised trials
  drink_menu/                   Original unfinished drink-menu prototype
```

The path from a request to the robot is:

```text
Human instruction → agent + skill library → gated phases → runtime → primitives → Viam
```

The role documents describe how your agent works in the repository; they do not
start background agents. `main.py` is an optional command-line shim, not the
primary way to use the project.

## Machine setup and configuration

Ask the agent to prepare the environment and check the configuration. The project
requires Python 3.11+; mock runs use only the standard library, while Viam access
and the existing trial tests require `requirements.txt`.

1. Install the dependencies into your project environment.
2. Copy `.env.example` to `.env` and fill in the machine address, API key, and key
   ID locally. Keep credentials out of chat and Git.
3. Have the agent run the read-only machine inspection to check resource names
   and configured frames.
4. Copy `config/demo.json` to `config/local.json` and set the resource bindings.
5. Derive the workspace bounds from the machine's own obstacle geometry with
   `python -m runtime --config config/local.json calibrate`. It reads the frame
   system, shrinks the free space by the configured tool's extent and a clearance
   margin, and prints which obstacle set each face. Re-run it with `--write` to
   save the bounds and mark the config calibrated. Review them first: they are
   only as right as the machine geometry behind them.
6. Have the agent check that every primitive needed by the plan is implemented
   before requesting a physical run.

Viam remains the source for hardware models, driver settings, frame geometry, and
module-specific torque/simulation settings. Local configuration holds task roles,
allowed objects, time limits, and the task workspace derived from that geometry.
Inspection reads resource names and frame configuration; it does not automatically
import arbitrary hardware settings, and neither command changes the machine. See
[the configuration guide](docs/configuration.md) for the boundary and Viam API references.

During execution, a failure, cancellation, or timeout requests StopAll and records
an aborted run. The agent diagnoses it and owns recovery within the task budget;
the runtime does not blindly retry or release. Primitive authors must check
physical preconditions and completion, including intermediate motion paths, and
expose observations for the agent's gates. Plan validation and command completion
do not establish that a drink was successfully made.

## Developer command reference

These are the tools the agent uses underneath the conversational workflow. You
can also run them manually from the repository root when debugging.

```sh
# Environment setup and read-only inspection
pip install -r requirements.txt
python -m runtime inspect

# Catalog, offline validation, and mock demos
python -m runtime tools
python -m runtime validate skill_library/entries/fixed_drink/v1/01_coffee.json
python -m runtime run skill_library/entries/fixed_drink/v1/01_coffee.json
# Whole-sequence integration example, without intermediate agent gates
python -m runtime validate demos/fixed.json
python -m runtime run demos/fixed.json
python -m runtime run demos/language.example.json

# Optional: generate planning input for an agent; this does not call an LLM
python -m runtime brief 'Pour coconut water into the cup; skip coffee and stirring'

# One physical phase, after the agent admits it; check its gate before the next
python -m runtime --config config/local.json run skill_library/entries/fixed_drink/v1/01_coffee.json --execute

# Tests with fake handlers/adapters; no hardware connection
python -m unittest discover -s tests -v
```

## Earlier experiments

The older attempts are preserved independently of the demo path, with their
tracks, profiles, local recordings, station configuration, and trial evidence.
Ask the agent to follow [agents/trial_operator.md](agents/trial_operator.md) when
working with them.

- [Record and replay](experiments/record_replay/README.md): teaching joint paths,
  replaying tracks, and smoothing recorded approaches.
- [Skill learning](experiments/skill_learning/README.md): supervised trials,
  outcome labels, parameter comparisons, and evidence-backed lessons.
- [Original drink menu](experiments/drink_menu/README.md): the earlier unfinished
  espresso, americano, and milk-coffee prototype.

These experiments are not automatically invoked by the demo. The team can
explicitly adapt a validated implementation into a primitive later. The agent can
aggregate applicable experimental findings into the shared skill library while
preserving their original evidence and context.

## Status and next steps

- [x] Agent instructions, shared primitive contracts, and task configuration
- [x] Fixed and language-plan examples with offline validation and mock execution
- [x] Agent fix-loop runbook, gating/recovery roles, and skill-library scaffold
- [x] Earlier experiments separated from the demo path
- [ ] Team-supplied primitive implementations and physical validation
- [ ] Reliable fixed coffee + coconut water + stirring demo
- [ ] Language-composed tasks validated on the robot
- [ ] Evidence-backed learned skills and recovery recipes from physical trials
- [ ] Later: amount control, froth detection, machine buttons, and drink presets
