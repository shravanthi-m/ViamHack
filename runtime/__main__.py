"""Agent/operator entry points. Run from the repository root with python -m runtime."""
import argparse
import asyncio
import json
import signal

from primitives.registry import catalog, implementations
from primitives.types import Context
from .config import DEFAULT_CONFIG, ROOT, read_json, validate_config
from .connection import connect
from .orchestrator import require_implementations, run_plan, validate_plan


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', default=str(DEFAULT_CONFIG), help='Full task config (no implicit merge)')
    sub = cli.add_subparsers(dest='command', required=True)
    sub.add_parser('tools', help='Print the primitive catalog and implementation readiness')
    brief = sub.add_parser('brief', help='Prepare a language-planning prompt for your coding/LLM agent')
    brief.add_argument('instruction')
    check = sub.add_parser('validate', help='Validate a plan offline')
    check.add_argument('plan')
    run = sub.add_parser('run', help='Mock by default; --execute calls team implementations')
    run.add_argument('plan', nargs='?', default=str(ROOT / 'demos/fixed.json'))
    run.add_argument('--execute', action='store_true')
    run.add_argument('--runs', default=str(ROOT / 'runs'))
    sub.add_parser('inspect', help='Read Viam resources and frame configuration; no movement')
    return cli


def check_resources(robot, config):
    expected = {'arm': ('component', 'arm'), 'gripper': ('component', 'gripper'),
                'camera': ('component', 'camera'), 'motion': ('service', 'motion')}
    available = {(r.type, r.subtype, r.name) for r in robot.resource_names}
    for role, name in config['resources'].items():
        if (*expected[role], name) not in available:
            raise ValueError(f'Configured {role} resource {name!r} not found; run inspect')


async def connected(args, config, plan=None):
    robot = await connect()
    try:
        if args.command == 'inspect':
            from google.protobuf.json_format import MessageToDict
            frames = await asyncio.wait_for(robot.get_frame_system_config(), 15)
            print(json.dumps({
                'resources': [dict(type=r.type, subtype=r.subtype, name=r.name) for r in robot.resource_names],
                'frames': [MessageToDict(f, preserving_proto_field_name=True) for f in frames],
            }, indent=2))
        else:
            check_resources(robot, config)
            print(await run_plan(plan, Context(config, robot), execute=True, runs=args.runs))
    finally:
        await asyncio.wait_for(robot.close(), 10)


async def dispatch(args):
    config = read_json(args.config)
    validate_config(config)
    if args.command == 'tools':
        print(json.dumps(catalog(config), indent=2))
    elif args.command == 'brief':
        print((ROOT / 'agents/orchestrator.md').read_text())
        print('\nPlanning input:\n' + json.dumps({
            'instruction': args.instruction, 'config': config, 'tools': catalog(config),
            'example_plan': read_json(ROOT / 'demos/fixed.json'),
        }, indent=2))
    elif args.command == 'inspect':
        await connected(args, config)
    else:
        plan = read_json(args.plan)
        execute = getattr(args, 'execute', False)
        validate_plan(plan, config, execute=execute)
        if args.command == 'validate':
            print(f'Valid plan: {len(plan["steps"])} steps. No connection or motion.')
        elif execute:
            require_implementations(plan, implementations())  # Fail before connecting.
            await connected(args, config, plan)
        else:
            print(await run_plan(plan, Context(config), runs=args.runs))
            print('Mock completed with fabricated observations. No connection or motion.')


def main():
    args = parser().parse_args()

    async def run():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        await dispatch(args)

    try:
        asyncio.run(run())
    except (ValueError, KeyError, FileNotFoundError, RuntimeError, TimeoutError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    main()
