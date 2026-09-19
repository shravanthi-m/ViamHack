"""Offline preparation of a fixed, supervised demo; never creates robot I/O."""
import hashlib
import shutil
from pathlib import Path

from primitives import gripper, replay_vision
from .config import ROOT, read_json
from .demonstrations import now, write
from .replay import COMPACT_TRACK, TRACK, adapted_plan, load_episode

OBJECTS = ('coconut_water', 'pitcher')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_files():
    return sorted([*ROOT.glob('runtime/*.py'), *ROOT.glob('primitives/*.py'),
                   ROOT / 'requirements.txt'])


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Episode image must remain inside its episode directory')
    return path


def prepare(config_path, demonstrations, out, *, compact=False):
    """Validate both episodes before writing; retain only replay data and checkpoints.

    The original teaching logs and phase videos stay in demonstrations/. The pack
    contains immutable-by-convention copies, with checks for accidental drift.
    A manifest is an integrity record, not a signature or physical certification.
    """
    config_path, out = Path(config_path).resolve(), Path(out).resolve()
    config = read_json(config_path)
    track_name = COMPACT_TRACK if compact else TRACK
    episodes = {name: load_episode(demonstrations, name, config, track_name=track_name)
                for name in OBJECTS}
    for _, meta, track in episodes.values():
        adapted_plan(meta, track, dict(dx=0, dy=0, dz=0), config)
    if replay_vision.profiles(config):
        for name, episode in episodes.items():
            replay_vision.load_profile(config, name, episode)
    # Never replace a rehearsed pack. Failures leave an incomplete pack without a
    # manifest, which prepared-demo refuses; originals are never changed.
    out.mkdir(parents=True, exist_ok=False)
    summaries = {}
    for name, (source, meta, track) in episodes.items():
        target = out / 'demonstrations' / name / source.name
        target.mkdir(parents=True)
        files = {'metadata.json', track_name}
        for observation in meta['observations'].values():
            for view in observation['frames'].values():
                for image in view['images']:
                    relative = image['path']
                    image_path = inside(source, relative)
                    if image.get('sha256') and digest(image_path) != image['sha256']:
                        raise ValueError(f'Teaching image changed: {image_path}')
                    files.add(relative)
        for relative in sorted(files):
            destination = inside(target, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inside(source, relative), destination)
        summaries[name] = dict(episode=source.name, source=str(source.resolve()),
                               format=track['format'], waypoints=track['waypoint_count'],
                               recorded_duration_s=track['duration_s'],
                               force_percent=meta.get('gripper_force_percent'),
                               force_evidence=meta.get('gripper_force_evidence', {}).get('source'))
        automatic = gripper.settings(config)['force_control'] == 'ufactory_atomic'
        summaries[name]['force_control'] = gripper.settings(config)['force_control']
        summaries[name]['replay_force_percent'] = (gripper.force_for_object(config, name) if automatic
                                                    else meta.get('gripper_force_percent'))
    external = {str(p): digest(p) for p in [config_path, *code_files()]}
    for filename in config.get('primitive_settings', {}).get('localization', {}).get('homographies', {}).values():
        path = (ROOT / filename).resolve()
        external[str(path)] = digest(path)
    for filename in replay_vision.profiles(config).values():
        path = (ROOT / filename).resolve()
        external[str(path)] = digest(path)
        for file in read_json(path)['evidence_sha256']:
            dependency = (ROOT / file).resolve()
            external[str(dependency)] = digest(dependency)
    manifest = dict(schema='prepared-demo/1', created_at=now(),
                    status='candidate_requires_physical_rehearsal', config=str(config_path),
                    compact=compact, episodes=summaries, external_sha256=external,
                    files_sha256={str(p.relative_to(out)): digest(p)
                                  for p in sorted(out.rglob('*')) if p.is_file()},
                    recovery_attempt_budget=0,
                    limitations=['Measured vision profiles or operator-measured offsets; all replay gates remain required.',
                                 'No automatic recovery, arbitrary starting state, or final home move.',
                                 'Offline validation is not physical success evidence.'])
    write(out / 'manifest.json', manifest)
    verify(out, config_path)
    return manifest


def verify(pack, config_path):
    """Refuse changed data, code, config, calibration or extra episode selection."""
    pack = Path(pack).resolve()
    manifest = read_json(pack / 'manifest.json')
    if manifest.get('schema') != 'prepared-demo/1':
        raise ValueError('Unsupported prepared demo manifest')
    if str(Path(config_path).resolve()) != manifest['config']:
        raise ValueError('Use the exact config pinned in the demo manifest')
    actual = {str(p.relative_to(pack)) for p in pack.rglob('*')
              if p.is_file() and p != pack / 'manifest.json'}
    if actual != set(manifest['files_sha256']):
        raise ValueError('Prepared demo file list changed; create and rehearse a new pack')
    for relative, expected in manifest['files_sha256'].items():
        if digest(inside(pack, relative)) != expected:
            raise ValueError(f'Prepared demo file changed: {relative}')
    expected_code = {str(p) for p in code_files()}
    pinned_code = {p for p in manifest['external_sha256']
                   if Path(p).parent in (ROOT / 'runtime', ROOT / 'primitives')
                   or Path(p) == ROOT / 'requirements.txt'}
    if expected_code != pinned_code:
        raise ValueError('Runtime file list changed; prepare and rehearse again')
    for filename, expected in manifest['external_sha256'].items():
        if digest(filename) != expected:
            raise ValueError(f'Demo dependency changed: {filename}; prepare and rehearse again')
    config = read_json(config_path)
    for name in OBJECTS:
        path, meta, track = load_episode(pack / 'demonstrations', name, config,
                                        track_name=COMPACT_TRACK if manifest['compact'] else TRACK)
        if path.name != manifest['episodes'][name]['episode']:
            raise ValueError('Prepared episode selection changed')
        adapted_plan(meta, track, dict(dx=0, dy=0, dz=0), config)
        if replay_vision.profiles(config):
            replay_vision.load_profile(config, name, (path, meta, track))
    return manifest
