"""One serial executor for fixed and agent-authored plans; never generates motor commands."""
import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from primitives import mock
from primitives.registry import TOOLS, implementations
from primitives.types import Context
from .config import fields, number, validate_config, validate_target, validate_yaw_pose


def validate_plan(plan, config, *, execute=False):
    validate_config(config, execute=execute)
    fields(plan, ('version', 'instruction', 'steps'), 'plan')
    if type(plan['version']) is not int or plan['version'] != 1:
        raise ValueError('Plan version must be 1')
    if not isinstance(plan['instruction'], str) or not plan['instruction'].strip():
        raise ValueError('Plan needs an instruction')
    if not isinstance(plan['steps'], list) or not 1 <= len(plan['steps']) <= config['limits']['max_steps']:
        raise ValueError('Plan must contain 1..max_steps steps')
    previous = {}
    for step in plan['steps']:
        fields(step, ('id', 'tool', 'args'), 'step')
        name, tool_name, args = step['id'], step['tool'], step['args']
        if not isinstance(name, str) or not name.isidentifier() or name in previous:
            raise ValueError('Step IDs must be unique identifiers')
        if not isinstance(tool_name, str) or tool_name not in TOOLS:
            raise ValueError(f'Unknown primitive: {tool_name}')
        spec = TOOLS[tool_name]
        fields(args, spec.parameters, f'{name}.args')
        for key, kind in spec.parameters.items():
            value = args[key]
            if kind == 'target':
                fields(value, ('$ref',), f'{name}.{key}')
                ref = value['$ref']
                if not isinstance(ref, str) or previous.get(ref) != 'localize':
                    raise ValueError(f'{name}.{key} must reference an earlier localize step')
            elif kind == 'object':
                if value not in config['objects']:
                    raise ValueError(f'Unknown object: {value}')
            elif kind == 'pose':
                validate_yaw_pose(value, config, execute=execute)
            elif kind == 'duration':
                number(value, 0, config['limits']['max_stir_duration_s'], 'duration_s')
                if value == 0:
                    raise ValueError('duration_s must be positive')
        previous[name] = tool_name


def require_implementations(plan, handlers):
    missing = sorted({step['tool'] for step in plan['steps']} - handlers.keys())
    if missing:
        raise ValueError('Team implementations missing: ' + ', '.join(missing))


async def run_plan(plan, ctx: Context, *, execute=False, runs='runs', handlers=None):
    # Freeze caller-owned input so handlers cannot change a later plan step or its policy.
    plan, config = copy.deepcopy(plan), copy.deepcopy(ctx.config)
    validate_plan(plan, config, execute=execute)
    handlers = implementations() if handlers is None else handlers
    if execute:
        require_implementations(plan, handlers)
        if ctx.robot is None:
            raise ValueError('Physical execution needs a connected robot')
    run = Path(runs) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8])
    run.mkdir(parents=True)

    def save(name, data):
        (run / name).write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')

    def event(data):
        with (run / 'events.jsonl').open('a') as out:
            out.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), **data), allow_nan=False) + '\n')

    save('plan.json', plan)
    save('config.json', config)
    results = {}
    outcome = dict(mode='execute' if execute else 'mock', status='running', results=results)
    save('result.json', outcome)
    try:
        async with asyncio.timeout(config['limits']['run_timeout_s']):
            for step in plan['steps']:
                name, tool = step['id'], step['tool']
                args = copy.deepcopy(step['args'])
                for key, kind in TOOLS[tool].parameters.items():
                    if kind == 'target':
                        args[key] = copy.deepcopy(results[args[key]['$ref']])
                        validate_target(args[key], config, execute=execute)
                event(dict(step=name, tool=tool, status='started', args=args))
                async with asyncio.timeout(config['limits']['primitive_timeout_s']):
                    call_ctx = Context(copy.deepcopy(config), ctx.robot if execute else None)
                    result = (await handlers[tool](call_ctx, **args) if execute
                              else await mock.invoke(tool, call_ctx, args))
                if not isinstance(result, dict):
                    raise ValueError(f'{tool} must return a JSON object; raise on failure')
                if tool == 'localize':
                    validate_target(result, config, execute=execute)
                    if result['object_id'] != args['object_id']:
                        raise ValueError('Localization returned a different object')
                # Serialize before accepting a result or starting any dependent step.
                event(dict(step=name, status='completed', result=result))
                results[name] = copy.deepcopy(result)
        outcome['status'] = 'completed'
    except BaseException as exc:
        outcome.update(status='aborted', error=f'{type(exc).__name__}: {exc}')
        if execute:
            try:
                await asyncio.wait_for(ctx.robot.stop_all(), 5)
                outcome['stop_requested'] = True
            except BaseException as stop_exc:
                outcome['stop_error'] = f'{type(stop_exc).__name__}: {stop_exc}'
        raise
    finally:
        save('result.json', outcome)
    return run
