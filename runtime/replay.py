"""Bounded translation of taught pickup, followed by unchanged recorded joint targets."""
import asyncio
import copy
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from primitives import gripper, motion, replay_vision
from primitives.camera import resolve as camera_resource_for_view
from .compaction import COMPACT_TRACK, TRACK
from .config import number, read_json, validate_pose
from .demonstrations import (DEFAULT_ROOT, PHASES, ask, confirm, event, joint_vector,
                             new_directory, now, require_station, station, stop_on_failure, write)


def estimate_object_offset(reference_frame, current_frame):
    """Extension point: return task-frame {dx,dy,dz} in mm plus reliability evidence.

    No reliable detector exists here. Do not substitute a zero offset or interpret
    overhead homography error as grasp accuracy. Reference/current observations
    include both cameras, tool pose, depth stream paths and timestamps.
    """
    raise NotImplementedError('Automatic object localization is unavailable; use supervised offset entry')


def bounded_offset(offset, *, max_xy_mm=20, max_z_mm=10):
    number(max_xy_mm, 0, 50, 'max_xy_mm')
    number(max_z_mm, 0, 20, 'max_z_mm')
    if not isinstance(offset, dict) or set(offset) != {'dx', 'dy', 'dz'}:
        raise ValueError('Offset must contain dx, dy, dz in task-frame millimetres')
    for value in offset.values():
        number(value, -10000, 10000, 'offset')
    if math.hypot(offset['dx'], offset['dy']) > max_xy_mm or abs(offset['dz']) > max_z_mm:
        raise ValueError('Object offset exceeds the allowed nearby translation')
    return offset


def load_episode(root, skill, config, episode_id=None, track_name=TRACK):
    require_station(config, skill)
    if not skill.isidentifier():
        raise ValueError('Skill name must be an identifier')
    if episode_id is not None and Path(episode_id).name != episode_id:
        raise ValueError('Episode ID must be a directory name')
    candidates = ([Path(root) / skill / episode_id] if episode_id else
                  sorted((Path(root) / skill).glob('episode_*'), reverse=True))
    for path in candidates:
        meta = read_json(path / 'metadata.json')
        if meta.get('status') != 'complete' or meta.get('success') is not True:
            if episode_id:
                raise ValueError('Episode is incomplete or failed')
            continue
        if not (path / track_name).exists():
            raise ValueError(f'{skill} has no {track_name} in {path.name}; derive it with the '
                             'compact command, or replay the full taught track')
        track = read_json(path / track_name)
        validate_episode(meta, track, skill, config)
        return path, meta, track
    raise ValueError(f'No successful complete episode for {skill}; teach it first')


def validate_episode(meta, track, skill, config, max_step_deg=20):
    if gripper.settings(config)['force_control'] == 'ufactory_atomic':
        gripper.force_for_object(config, skill)
    if (meta.get('schema') != 'teach-episode/1' or meta.get('object') != skill
            or meta.get('status') != 'complete' or meta.get('success') is not True
            or meta.get('grasp_success') is not True):
        raise ValueError('Episode identity, completion or grasp evidence is invalid')
    if meta.get('station') != station(config):
        raise ValueError('Episode station/frame/camera/calibration differs from current config')
    if 'gripper_force_percent' in meta:
        number(meta['gripper_force_percent'], 0.01, gripper.max_force(config), 'taught gripper force percent')
    for label in ('pregrasp_pose', 'grasp_pose', 'place_pose'):
        validate_pose(meta[label], config, execute=True)
    if (track.get('format') not in ('joint-track/1', 'pose-track/1') or track.get('units') != 'degrees'
            or track.get('arm') != config['resources']['arm'] or track.get('frame') != config['frame']):
        raise ValueError('Unsupported trajectory format, units, arm or frame')
    rows = track.get('waypoints')
    if not isinstance(rows, list) or not rows or track.get('waypoint_count') != len(rows):
        raise ValueError('Missing or inconsistent trajectory waypoints')
    previous, sequence = None, []
    for row in rows:
        validate_pose(row['pose'], config, execute=True)
        number(row['t'], 0, 86400, 'waypoint time')
        phase = row['phase']
        if not sequence or phase != sequence[-1]:
            sequence.append(phase)
        if previous and row['t'] < previous['t']:
            raise ValueError('Trajectory timestamps go backwards')
        if track['format'] == 'joint-track/1':
            joint_vector(row['joints'], track['joint_count'])
            if previous and max(abs(a-b) for a,b in zip(previous['joints'], row['joints'])) > max_step_deg:
                raise ValueError('Recorded joint step exceeds replay limit; reteach more densely')
        previous = row
    if sequence != list(PHASES):
        raise ValueError('Trajectory must contain every phase exactly once and in order')
    if rows[-1]['t'] != track['duration_s']:
        raise ValueError('Inconsistent trajectory duration')
    if next(row for row in reversed(rows) if row['phase'] == 'place')['pose'] != meta['place_pose']:
        raise ValueError('Place pose differs from release waypoint')
    for label in ('before', 'grasp', 'after_grasp', 'after_place'):
        if not {'wrist', 'overhead'} <= meta['observations'][label]['frames'].keys():
            raise ValueError('Missing teaching camera context')


def translated(pose, offset):
    result = copy.deepcopy(pose)
    for axis in ('x', 'y', 'z'):
        result[axis] += offset['d' + axis]
    return result


def adapted_plan(meta, track, offset, config, *, max_xy_mm=20, max_z_mm=10):
    offset = bounded_offset(offset, max_xy_mm=max_xy_mm, max_z_mm=max_z_mm)
    result = dict(pregrasp=translated(meta['pregrasp_pose'], offset),
                  grasp=translated(meta['grasp_pose'], offset), waypoints=copy.deepcopy(track['waypoints']))
    for row in result['waypoints']:
        if row['phase'] == 'lift':
            row['pose'] = translated(row['pose'], offset)
        row['replay_mode'] = ('pose' if row['phase'] == 'lift' and any(offset.values())
                              or track['format'] == 'pose-track/1' else 'joints')
        validate_pose(row['pose'], config, execute=True)
    for label in ('pregrasp', 'grasp'):
        validate_pose(result[label], config, execute=True)
    return result


async def supervised_scene(skill, episode, io, directory, config, ask_fn=ask):
    path, meta, _ = episode
    await confirm(f'{skill}: arm stationary, empty hand, object upright with demonstrated orientation; '
                  'same object height and unchanged overhead camera mounting; '
                  'fixed cup and taught return spot clear, free-drive OFF, entire recorded swept path clear?', ask_fn)
    observation = await io.capture(directory, skill + '_current')
    event(directory, 'localization_context', reference=str(path / 'metadata.json'), current=observation)
    if replay_vision.profiles(config):
        frame = observation['frames']['overhead']
        images = [im for im in frame['images'] if im.get('mime_type') in ('image/jpeg', 'image/png', 'image/webp')]
        if len(images) != 1 or frame.get('camera') != camera_resource_for_view(config, 'overhead'):
            raise ValueError('Vision replay needs one image from the configured overhead camera')
        current = (Path(directory)/images[0]['path']).resolve()
        if not current.is_relative_to(Path(directory).resolve()):
            raise ValueError('Vision image must remain inside the current run')
        try:
            offset, evidence = await asyncio.to_thread(replay_vision.offset, config, skill, episode,
                                                       current, frame['captured_at'])
        except ValueError as exc:
            event(directory, 'vision_grasp_blocked', object=skill, reason=str(exc))
            raise
        event(directory, 'vision_grasp_offset', object=skill, offset=offset, evidence=evidence)
        await confirm(f'Vision measured {skill} translation: dx={offset["dx"]:.2f}, '
                      f'dy={offset["dy"]:.2f} mm (fixed height). Correct object, orientation '
                      'and adjusted approach path clear?', ask_fn)
        return dict(object=skill, frame=config['frame'], observation=observation, offset=offset,
                    reliable=True, source='measured_patch_translation', timestamp=frame['captured_at'])
    print(f'Compare reference images in {path} with current images in {directory}.\n'
          'No automatic localization: enter a measured task-frame translation in mm.\n'
          'Do not guess from the uncorrected overhead homography. Enter q if uncertain.', flush=True)
    values = [float(part) for part in (await ask_fn('Measured dx,dy,dz (0,0,0 only if verified unchanged):')).split(',')]
    if len(values) != 3:
        raise ValueError('Expected exactly three translation values')
    await confirm('Translation measured reliably; same object orientation and grasp geometry?', ask_fn)
    return dict(object=skill, frame=config['frame'], observation=observation,
                offset=dict(zip(('dx', 'dy', 'dz'), values)), reliable=True,
                source='operator_measured_and_confirmed', timestamp=now())


async def replay_skill(skill_name, current_scene, *, io, config, episode, directory,
                       force_percent=None, pause_s=0.2, max_xy_mm=20, max_z_mm=10,
                       max_step_deg=20, ask_fn=ask):
    """One attempt only. Any error/cancellation stops all; never releases on failure."""
    try:
        require_station(config, skill_name)
        number(pause_s, 0, 10, 'pause_s')
        number(max_step_deg, 0.01, 20, 'max_step_deg')
        path, meta, track = episode
        validate_episode(meta, track, skill_name, config, max_step_deg)
        automatic = gripper.settings(config)['force_control'] == 'ufactory_atomic'
        if automatic:
            configured_force = gripper.force_for_object(config, skill_name)
            if force_percent is not None and force_percent != configured_force:
                raise ValueError('Replay force differs from configured object setting')
            force_percent = configured_force
        elif force_percent is None:
            force_percent = meta.get('gripper_force_percent')
        number(force_percent, 0.01, gripper.max_force(config), 'force_percent')
        if (current_scene.get('object') != skill_name or current_scene.get('frame') != config['frame']
                or current_scene.get('reliable') is not True or not current_scene.get('source')):
            raise ValueError('Current localization identity/frame/reliability is unknown')
        for stamp in (current_scene['timestamp'], current_scene['observation']['timestamp']):
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
            maximum_age = 30 if current_scene.get('source') == 'measured_patch_translation' else 300
            if not 0 <= age <= maximum_age:
                raise ValueError('Current scene is stale; observe again')
        offset = current_scene.get('offset')
        if offset is None:
            offset = estimate_object_offset(meta['observations']['before'], current_scene['observation'])
        plan = adapted_plan(meta, track, offset, config, max_xy_mm=max_xy_mm, max_z_mm=max_z_mm)
        write(directory / f'{skill_name}_plan.json', dict(episode=str(path), scene=current_scene, plan=plan))
        event(directory, 'replay_' + skill_name, offset=offset, force_percent=force_percent)
        if await io.holding() is not False:
            raise RuntimeError('Replay requires a verified empty hand')
        await io.prepare()
        await io.open()
        await io.pose(plan['pregrasp'])
        await asyncio.sleep(pause_s)
        await io.pose(plan['grasp'])
        if not automatic and meta.get('gripper_force_evidence', {}).get('source') == 'operator_entered':
            await confirm(f'Close the gripper manually with your controls at the saved '
                          f'force {force_percent:g}%. Is the intended object securely grasped?', ask_fn)
            event(directory, 'manual_grasp_confirmed', force_percent=force_percent,
                  force_source='operator_confirmation', hardware_force_verified=False)
        else:
            event(directory, 'automatic_grasp_requested', object=skill_name,
                  force_percent=force_percent,
                  force_source='object_setting' if automatic else 'taught_or_explicit')
            result = await io.close(force_percent)
            event(directory, 'automatic_grasp_completed', object=skill_name, evidence=result)
        if await io.holding() is not True:
            raise RuntimeError('Grasp holding state unknown or empty')
        await confirm('Grasp succeeded on the intended object?', ask_fn)
        event(directory, 'grasp_confirmed', observation=await io.capture(directory, skill_name + '_after_grasp'))
        for phase in PHASES:
            event(directory, 'replay_phase_' + phase)
            rows = [row for row in plan['waypoints'] if row['phase'] == phase]
            started = time.monotonic()
            for row in rows:
                await asyncio.sleep(max(0, started + row['t'] - rows[0]['t'] - time.monotonic()))
                if phase != 'retract' and await io.holding() is not True:
                    raise RuntimeError('Lost grasp during replay')
                if row['replay_mode'] == 'pose':
                    await io.pose(row['pose'])
                else:
                    state = await io.state()
                    joint_vector(state['joints'], track['joint_count'])
                    if max(abs(a-b) for a,b in zip(state['joints'], row['joints'])) > max_step_deg:
                        raise RuntimeError('Live joint transition exceeds replay limit; stop and inspect')
                    await io.joints(row['joints'])
                    # Check the tool too: the taught joint vector must still mean the taught pose.
                    reached = await io.state()
                    error = motion.deviation(reached['pose'], row['pose'])
                    tuning = motion.settings(config)
                    if (error['position_error_mm'] > tuning['position_tolerance_mm'] or
                            error['orientation_error_deg'] > tuning['orientation_tolerance_deg']):
                        raise RuntimeError('Joint replay tool pose differs from teaching')
            if phase == 'place':
                await confirm('Object supported at the original taught place; safe to release?', ask_fn)
                await io.open()
                if await io.holding() is not False:
                    raise RuntimeError('Release not verified')
                event(directory, 'after_place', observation=await io.capture(directory, skill_name + '_after_place'))
            await asyncio.sleep(pause_s)
        if await io.holding() is not False:
            raise RuntimeError('Final empty-hand state not verified')
        await confirm('Pour completed, cup stable, source returned upright, hand empty?', ask_fn)
        event(directory, 'skill_completed', object=skill_name, evidence='operator_report_and_gripper')
    except BaseException as exc:
        await stop_on_failure(io, directory, exc)
        raise


async def run_demo(io, config, *, root=DEFAULT_ROOT, runs='runs/teach_replay', execute=False,
                   ask_fn=ask, **options):
    # Admit both datasets before any connection-driven motion. Observe pitcher anew
    # after coconut: a previous camera observation is not the next phase's state.
    number(options.get('pause_s', 0.2), 0, 10, 'pause_s')
    number(options.get('max_step_deg', 20), 0.01, 20, 'max_step_deg')
    track_name = COMPACT_TRACK if options.pop('compact', False) else TRACK
    episodes = {name: load_episode(root, name, config, track_name=track_name)
                for name in ('coconut_water', 'pitcher')}
    for _, meta, track in episodes.values():
        validate_episode(meta, track, meta['object'], config, options.get('max_step_deg', 20))
        adapted_plan(meta, track, dict(dx=0, dy=0, dz=0), config,
                     max_xy_mm=options.get('max_xy_mm', 20), max_z_mm=options.get('max_z_mm', 10))
    if replay_vision.profiles(config):
        for name, episode in episodes.items():
            replay_vision.load_profile(config, name, episode)
    if not execute:
        print('Offline validation passed for both episodes. No connection or motion. '
              'Live localization and physical outcome gates remain required.')
        return None
    directory = new_directory(runs, 'demo')
    outcome = dict(status='running', started_at=now(), recovery_attempt_budget=0)
    write(directory / 'result.json', outcome)
    try:
        async with asyncio.timeout(config['limits']['run_timeout_s']):
            for name, episode in episodes.items():
                automatic = gripper.settings(config)['force_control'] == 'ufactory_atomic'
                force = (gripper.force_for_object(config, name) if automatic
                         else episode[1].get('gripper_force_percent'))
                if force is None:
                    force = float(await ask_fn(f'{name}: older episode has no saved force; '
                                              'enter the team-approved gripper force percent (no default):'))
                else:
                    event(directory, 'using_object_gripper_force' if automatic else 'using_taught_gripper_force',
                          object=name, force_percent=force)
                scene = await supervised_scene(name, episode, io, directory, config, ask_fn)
                await replay_skill(name, scene, io=io, config=config, episode=episode,
                                   directory=directory, force_percent=force, ask_fn=ask_fn, **options)
            outcome.update(status='completed', success_source='operator_report_and_gripper')
    except BaseException as exc:
        # Covers failures before replay_skill as well (capture, prompts, timeout).
        await stop_on_failure(io, directory, exc)
        outcome.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        outcome['finished_at'] = now()
        write(directory / 'result.json', outcome)
    return directory
