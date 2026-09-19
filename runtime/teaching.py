"""Read-only teaching: the operator owns hand guidance, gripper, and free-drive."""
import asyncio
import copy
import json
import fcntl
import shutil
from pathlib import Path
from uuid import uuid4
import time

from primitives import gripper

from .demonstrations import (DEFAULT_ROOT, PHASES, ask, confirm, event, new_directory,
                             now, require_station, station, stop_on_failure, write)
from .config import number, read_json


async def capture_frames(io, directory, phase, started, camera_hz, stopped, attempt):
    """Independent camera cadence: slow image RPCs do not stall joint sampling."""
    frames = directory / 'frames' / phase / attempt
    frames.mkdir(parents=True)
    index = 0
    while not stopped.is_set():
        tick = time.monotonic()
        observation = await io.capture(frames, f'{index:06d}')
        # Paths are relative to the episode, like the checkpoint observations.
        for frame in observation['frames'].values():
            for item in frame['images']:
                item['path'] = str(frames.relative_to(directory) / item['path'])
        with (directory / 'frames.jsonl').open('a') as out:
            out.write(json.dumps(dict(phase=phase, attempt=attempt, t=tick-started,
                                      completed_t=time.monotonic()-started,
                                      observation=observation), allow_nan=False) + '\n')
        index += 1
        try:
            await asyncio.wait_for(stopped.wait(), max(0.001, 1/camera_hz - (time.monotonic()-tick)))
        except TimeoutError:
            pass


async def record_phase(io, directory, phase, waypoints, started, hz, ask_fn, camera_hz=2):
    await ask_fn(f'Prepare {phase}. Press Enter to start sampling; then hand-guide the arm.')
    attempt = uuid4().hex[:8]
    event(directory, 'record_' + phase, attempt=attempt)
    stopped = asyncio.Event()

    async def sample():
        while True:
            tick = time.monotonic()
            state = await io.state()
            row = dict(t=time.monotonic() - started, phase=phase, attempt=attempt, **state)
            waypoints.append(row)
            with (directory / 'samples.jsonl').open('a') as out:
                out.write(json.dumps(row, allow_nan=False) + '\n')
            if stopped.is_set():
                return state
            try:
                await asyncio.wait_for(stopped.wait(), max(0.001, 1/hz - (time.monotonic()-tick)))
            except TimeoutError:
                pass

    finish = asyncio.create_task(ask_fn(f'Press Enter when {phase} is complete (q aborts):'))
    samples = asyncio.create_task(sample())
    cameras = asyncio.create_task(capture_frames(io, directory, phase, started, camera_hz, stopped, attempt))
    try:
        done, _ = await asyncio.wait({finish, samples, cameras}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task  # Prompt aborts and non-network errors propagate immediately.
        stopped.set()
        # Enter waits for an in-flight observation to recover; q/Ctrl-C cancels it.
        result = await samples
        await cameras
        return result
    finally:
        stopped.set()
        for task in (finish, samples, cameras):
            task.cancel()
        await asyncio.gather(finish, samples, cameras, return_exceptions=True)


def resume_episode(root, skill, resume, config):
    if resume != 'latest' and (Path(resume).name != resume or resume in ('.', '..')):
        raise ValueError('Resume takes latest or an episode directory name')
    paths = (sorted((Path(root)/skill).glob('episode_*'), reverse=True) if resume == 'latest'
             else [Path(root)/skill/resume])
    for directory in paths:
        meta = read_json(directory/'metadata.json')
        if meta.get('status') == 'complete':
            if resume == 'latest':
                continue
            raise ValueError('A completed episode cannot be resumed')
        if meta.get('object') != skill or meta.get('station') != station(config):
            raise ValueError('Resume episode object/station differs from current config')
        if 'completed_phases' not in meta:
            # Older recordings saved after every phase but had no explicit cursor.
            # Conservatively repeat the last phase whose start was logged.
            entries = []
            log = directory/'events.jsonl'
            if log.exists():
                for line in log.read_text().splitlines():
                    try:
                        step = json.loads(line).get('step', '')
                    except json.JSONDecodeError:
                        continue
                    if step.startswith('record_') and step[7:] in PHASES:
                        entries.append(step[7:])
            meta['completed_phases'] = list(dict.fromkeys(entries[:-1]))
        completed = meta['completed_phases']
        if completed != list(PHASES[:len(completed)]):
            raise ValueError('Completed phase checkpoint is not a valid prefix')
        track = directory/'trajectory.json'
        rows = read_json(track)['waypoints'] if track.exists() else []
        selected = [row for row in rows if row['phase'] in completed]
        if set(completed) != {row['phase'] for row in selected}:
            raise ValueError('Completed phase checkpoint is missing trajectory data')
        return directory, meta, selected
    raise ValueError(f'No interrupted episode to resume for {skill}')


async def teach_skill(skill_name, io, config, *, root=DEFAULT_ROOT, hz=5, grasp_type='side',
                      notes='', camera_hz=2, ask_fn=ask, resume=None):
    require_station(config, skill_name)
    number(hz, 1, 20, 'sample hz')
    number(camera_hz, 0.1, 5, 'camera hz')
    if resume:
        directory, metadata, waypoints = resume_episode(root, skill_name, resume, config)
    else:
        directory = new_directory(root, skill_name)
        metadata = dict(schema='teach-episode/1', object=skill_name, episode_id=directory.name,
                        created_at=now(), status='incomplete', success=False, grasp_success=False,
                        grasp_type=grasp_type, notes=notes, station=station(config),
                        pose_units='mm', orientation='Viam orientation vector; theta degrees',
                        pose_component=config['resources']['gripper'], joint_units='degrees',
                        requested_hz=hz, requested_camera_hz=camera_hz, frame_log='frames.jsonl',
                        observations={}, joint_positions={}, completed_phases=[])
        waypoints = []
    with (directory/'.recording.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This episode is already being recorded by another process') from None
        if resume:
            backup = directory/'interruptions'/uuid4().hex[:8]
            backup.mkdir(parents=True)
            for name in ('metadata.json', 'trajectory.json'):
                if (directory/name).exists():
                    shutil.copy2(directory/name, backup/name)
        if hasattr(io, 'directory'):
            io.directory = directory
        return await _teach_skill(skill_name, io, config, directory, metadata, waypoints,
                                  hz=hz, camera_hz=camera_hz, ask_fn=ask_fn, resumed=bool(resume))


async def _teach_skill(skill_name, io, config, directory, metadata, waypoints, *,
                       hz, camera_hz, ask_fn, resumed):
    metadata.update(status='recording', success=False, requested_hz=hz, requested_camera_hz=camera_hz)
    metadata.pop('error', None)
    def save():
        if waypoints:
            has_joints = all(row['joints'] is not None for row in waypoints)
            normalized = copy.deepcopy(waypoints)
            for row in normalized:
                row['t'] -= waypoints[0]['t']
            stats = {}
            if has_joints:
                columns = list(zip(*(row['joints'] for row in normalized)))
                stats = dict(joint_min=[min(c) for c in columns], joint_max=[max(c) for c in columns],
                             joint_travel_deg=[sum(abs(b-a) for a,b in zip(c, c[1:])) for c in columns],
                             max_step_deg=max((max(abs(b-a) for a,b in zip(x['joints'], y['joints']))
                                               for x,y in zip(normalized, normalized[1:])), default=0))
            write(directory / 'trajectory.json', dict(
                format='joint-track/1' if has_joints else 'pose-track/1', name=skill_name,
                arm=config['resources']['arm'], units='degrees', frame=config['frame'],
                joint_count=len(waypoints[0]['joints']) if has_joints else None,
                waypoint_count=len(normalized), duration_s=normalized[-1]['t'], stats=stats, waypoints=normalized))
        write(directory / 'metadata.json', metadata)  # Commit the cursor after the trajectory.
    save()
    event(directory, 'teaching_resumed' if resumed else 'teaching_started')
    try:
        if resumed:
            pending = next((p for p in PHASES if p not in metadata['completed_phases']), 'final confirmation')
            await confirm(f'Resuming {directory.name}; completed phases are kept. Next: {pending}. '
                          'Restore the object, gripper and arm manually to the start of the pending step. '
                          'If a pour was interrupted, restore its starting contents too. '
                          'Is the setup known and ready? No automatic motion will occur.', ask_fn)
        if 'before' not in metadata['observations']:
            await confirm('Place the object in its starting region; arm stationary, gripper empty. '
                          'You control free-drive and gripper manually. Ready?', ask_fn)
            metadata['observations']['before'] = await io.capture(directory, 'before')
            save()
        for label in ('pregrasp', 'grasp'):
            if label + '_pose' in metadata and (label != 'grasp' or 'grasp' in metadata['observations']):
                continue
            if label == 'pregrasp':
                print('Pregrasp = a clear approach pose before reaching the object. '
                      'Your home pose is fine if it gives a safe approach.', flush=True)
            await ask_fn(f'Hand-guide to {label}; hold still. Press Enter to record {label} pose:')
            state = await io.state()
            metadata[label + '_pose'] = state['pose']
            metadata['joint_positions'][label] = state['joints']
            metadata[label + '_state'] = state
            if label == 'grasp':
                metadata['observations']['grasp'] = await io.capture(directory, 'grasp')
            event(directory, label + '_recorded')
            save()
        if not metadata.get('grasp_success') or 'gripper_force_percent' not in metadata:
            event(directory, 'waiting_for_gripper_force')
            await confirm('Close the gripper using your controls and adjust its force until the grasp '
                          'is secure without damaging the object. Take as long as needed. '
                          'Is the force right and the grasp successful?', ask_fn)
            while True:
                value = await ask_fn(f'Enter the force percent you set (greater than 0, '
                                     f'at most {gripper.max_force(config):g}; q aborts):')
                try:
                    force = float(value)
                    number(force, 0.01, gripper.max_force(config), 'taught gripper force percent')
                    break
                except ValueError:
                    print('Enter a valid force percentage within the configured limit.', flush=True)
            grasp = dict(force_percent=force, holding=True, timestamp=now(),
                         source='operator_entered', holding_source='operator_confirmation',
                         hardware_verified=False)
            metadata['gripper_force_percent'] = grasp['force_percent']
            metadata['gripper_force_evidence'] = grasp
            metadata['grasp_success'] = True
            event(directory, 'gripper_force_recorded', grasp=grasp)
            save()
        if 'after_grasp' not in metadata['observations']:
            metadata['observations']['after_grasp'] = await io.capture(directory, 'after_grasp')
            save()
        started = time.monotonic() - (waypoints[-1]['t'] + 1 if waypoints else 0)
        for phase in PHASES:
            if phase in metadata['completed_phases']:
                continue
            metadata['active_phase'] = phase
            if hasattr(io, 'phase'):
                io.phase = phase
            save()
            while True:
                recovery_count = getattr(io, 'recoveries', 0)
                phase_start = len(waypoints)
                state = await record_phase(io, directory, phase, waypoints, started, hz, ask_fn, camera_hz)
                if getattr(io, 'recoveries', 0) == recovery_count:
                    break
                event(directory, 'phase_has_connection_gap_repeat_required', phase=phase)
                # All partial rows/images remain in append-only logs; do not splice
                # missing robot motion into the trajectory selected for replay.
                del waypoints[phase_start:]
                save()
                await confirm(f'{phase} had a connection gap. Its partial data is saved. '
                              "Restore this phase's starting arm/object/gripper state manually "
                              '(and contents if pouring), then confirm to record only this phase again.', ask_fn)
            if phase == 'place':
                metadata['place_pose'] = state['pose']
                metadata['joint_positions']['place'] = state['joints']
                save()
                await confirm('Object supported at the taught return location? Open gripper manually '
                              'and confirm released and stable.', ask_fn)
                metadata['observations']['after_place'] = await io.capture(directory, 'after_place')
            metadata['completed_phases'].append(phase)
            metadata['active_phase'] = None
            save()
        await confirm('Was the entire manipulation successful, with object placed and hand empty?', ask_fn)
        metadata.update(status='complete', success=True, completed_at=now(),
                        success_source='operator_report', validation_status='candidate')
        event(directory, 'teaching_completed')
    except BaseException as exc:
        metadata.update(status='paused', error=f'{type(exc).__name__}: {exc}')
        await stop_on_failure(io, directory, exc)
        raise
    finally:
        save()
    return directory
