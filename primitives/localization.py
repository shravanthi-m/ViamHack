"""Perception team: camera/detection/frame transforms belong here.

Detection lives in vision.py. This module turns a measured image anchor into a
manipulation target only when the station has a validated object profile.

That step is a hand-eye calibration: a 3x3 planar homography, measured once per
camera view and stored in its own JSON artifact, that maps overhead image pixels to
task-frame x/y in millimetres. A homography is planar, so it answers "where on the
calibrated surface" and nothing about height: z for a move is a task decision
(`hover_z_mm`), never a perception result. It is also only as good as it was
measured -- `accuracy_mm` travels with every conversion so a caller can gate on it
instead of assuming the number is exact.
"""
import json
import math
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from . import camera, vision
from .types import Context, PoseYaw, Target

IMPLEMENTED = {'localize'}

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = {
    'homographies': {},  # Camera view name -> calibration artifact path. No view, no mapping.
    'hover_z_mm': None,  # Task-frame z for a move over a detection. Deliberately unset.
    'target_profiles': {},  # object_id -> human-measured profile artifact; never invented.
    'max_observation_age_s': 60,
}


def number(value, low, high, name):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not low <= value <= high):
        raise ValueError(f'{name} must be a finite number in [{low}, {high}]')


def settings(config):
    """Validate the team-owned primitive_settings.localization block over the defaults."""
    supplied = config.get('primitive_settings', {}).get('localization', {})
    if not isinstance(supplied, dict) or not set(supplied) <= set(DEFAULT_SETTINGS):
        raise ValueError('primitive_settings.localization accepts only: '
                         + ', '.join(sorted(DEFAULT_SETTINGS)))
    values = {**DEFAULT_SETTINGS, **supplied}
    named = values['homographies']
    if not isinstance(named, dict):
        raise ValueError('primitive_settings.localization.homographies maps a camera view '
                         'to its calibration file, for example '
                         '{"overhead": "config/overhead_homography.json"}')
    declared = camera.views(config)  # A calibration is for a view the station declares.
    for view, path in named.items():
        if view not in declared:
            raise ValueError(f'Hand-eye calibration names camera view {view!r}, which this '
                             'station does not declare; its views are: '
                             + ', '.join(sorted(declared)))
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f'primitive_settings.localization.homographies.{view} must be '
                             'the path to a calibration file')
    if values['hover_z_mm'] is not None:
        number(values['hover_z_mm'], -10000, 10000,
               'primitive_settings.localization.hover_z_mm')
    profiles = values['target_profiles']
    if (not isinstance(profiles, dict) or any(
            key not in config['objects'] or not isinstance(path, str) or not path.strip()
            for key, path in profiles.items())):
        raise ValueError('localization.target_profiles must map configured objects to profile files')
    number(values['max_observation_age_s'], 1, 120, 'max_observation_age_s')
    return {**values, 'homographies': dict(named), 'target_profiles': dict(profiles)}


# --- Hand-eye calibration -----------------------------------------------------

def matrix(raw, name):
    """The 3x3 homography from an artifact, checked for the shape the maths needs."""
    if not isinstance(raw, list) or len(raw) != 3 or any(
            not isinstance(row, list) or len(row) != 3 for row in raw):
        raise ValueError(f'{name}: H must be a 3x3 matrix of numbers')
    rows = tuple(tuple(float(value) for value in row) for row in raw
                 if all(type(value) in (int, float) and math.isfinite(value) for value in row))
    if len(rows) != 3:
        raise ValueError(f'{name}: H must be a 3x3 matrix of finite numbers')
    (a, b, c), (d, e, f), (g, h, i) = rows
    determinant = (a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g))
    if abs(determinant) < 1e-12:
        raise ValueError(f'{name}: H is singular, so it maps no plane to the task frame')
    return rows


def calibration(config, view):
    """Load and check the calibration artifact for one camera view.

    The artifact is whatever the calibration procedure wrote. Only `H` is required,
    so a regenerated file still loads; the rest is provenance we validate when it is
    there rather than assume. A declared frame must be the frame the task runs in --
    a homography measured against a different frame is wrong, not merely unlabelled.
    """
    named = settings(config)['homographies']
    if view not in named:
        raise ValueError(f'No hand-eye calibration for the {view!r} camera view; '
                         'calibrated views are: ' + (', '.join(sorted(named)) or 'none'))
    path = Path(named[view])
    path = path if path.is_absolute() else ROOT / path
    name = f'{path.name} ({view} view)'
    if not path.exists():
        raise ValueError(f'{name}: calibration file not found at {path}')
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f'{name}: calibration file is not valid JSON: {exc}') from exc
    if not isinstance(raw, dict) or 'H' not in raw:
        raise ValueError(f'{name}: calibration file must be an object with an "H" matrix')
    frame = raw.get('frame')
    if frame is not None and frame != config['frame']:
        raise ValueError(f'{name}: calibrated against frame {frame!r}, but the task frame '
                         f'is {config["frame"]!r}')
    units = raw.get('units')
    if units is not None and units != 'mm':
        raise ValueError(f'{name}: calibration is in {units!r}; this scaffold is millimetres')
    accuracy = {key: raw[key] for key in ('mean_error_mm', 'max_error_mm') if key in raw}
    for key, value in accuracy.items():
        number(value, 0, 10000, f'{name}: {key}')
    size = raw.get('image_size')
    if size is not None and (not isinstance(size, list) or len(size) != 2 or any(
            type(value) is not int or not 1 <= value <= 16384 for value in size)):
        raise ValueError(f'{name}: image_size must be measured [width, height] in pixels')
    return {'H': matrix(raw['H'], name), 'view': view, 'source': named[view],
            'accuracy_mm': accuracy, 'image_size': size,
            'frame': raw.get('frame'), 'units': raw.get('units'),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def project(H, px, py):
    """Apply the homography: pixel (px, py) -> (x, y) in millimetres.

    The third row is what makes it projective rather than affine -- it divides out
    the perspective of a camera looking at the plane from an angle. Its value is
    zero only on the image's horizon line, where the plane is edge-on and no finite
    point on it exists.
    """
    for value, name in ((px, 'px'), (py, 'py')):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'{name} must be a finite pixel coordinate')
    scale = H[2][0] * px + H[2][1] * py + H[2][2]
    if abs(scale) < 1e-12:
        raise ValueError(f'Pixel ({px}, {py}) is on the calibration horizon and maps to no '
                         'point on the plane')
    return ((H[0][0] * px + H[0][1] * py + H[0][2]) / scale,
            (H[1][0] * px + H[1][1] * py + H[1][2]) / scale)


def scale_mm_per_px(H, px, py, *, step=0.5):
    """How many millimetres a pixel is worth near (px, py), along each image axis.

    A homography is not a constant scale: perspective makes the far side of the
    plane finer than the near side, so a pixel length only converts to millimetres
    at a stated place. Use it to size what a detector measured in pixels, not to
    convert a distance that spans the image.
    """
    here = project(H, px, py)
    across = project(H, px + step, py)
    down = project(H, px, py + step)
    return {'x_axis_mm_per_px': math.dist(here, across) / step,
            'y_axis_mm_per_px': math.dist(here, down) / step}


def within(config, x, y):
    """Whether a mapped point is inside the calibrated workspace the executor enforces."""
    bounds = config.get('workspace_mm') or {}
    return all(isinstance(bounds.get(axis), list) and len(bounds[axis]) == 2
               and bounds[axis][0] <= value <= bounds[axis][1]
               for axis, value in (('x', x), ('y', y)))


def pixel_to_frame(config, view, px, py) -> dict:
    """Where a pixel of a camera view sits in the task frame, with its accuracy.

    Returns task-frame `x`/`y` in millimetres on the calibrated plane, the local
    millimetres-per-pixel scale there, the calibration's own reported error, and
    whether the point is inside the workspace bounds. It reports; it never moves and
    never decides that a detection is good enough.
    """
    measured = calibration(config, view)
    x, y = project(measured['H'], px, py)
    return {'view': view, 'frame': config['frame'], 'pixel': [px, py], 'x': x, 'y': y,
            'in_workspace': within(config, x, y),
            'scale': scale_mm_per_px(measured['H'], px, py),
            'accuracy_mm': measured['accuracy_mm'], 'calibration': measured['source']}


def hover_pose(config, x, y, *, z=None, yaw=0.0) -> PoseYaw:
    """The go_to_pose argument that puts the tool over a task-frame x/y, pointing down.

    Height is the one number the calibration cannot supply: a planar homography knows
    the surface, not what is standing on it. `z` comes from the caller or from
    `primitive_settings.localization.hover_z_mm`, and has to clear the tallest thing
    on the plane, not merely the plane. Raising here beats discovering it with the
    gripper.
    """
    height = settings(config)['hover_z_mm'] if z is None else z
    if height is None:
        low, high = (config.get('workspace_mm') or {}).get('z') or (None, None)
        raise ValueError(
            'No hover height: set primitive_settings.localization.hover_z_mm, or pass z. '
            + (f'The calibrated workspace allows z in [{low}, {high}] mm; pick a height that '
               'clears the tallest object on the plane.' if low is not None else ''))
    number(height, -10000, 10000, 'hover z')
    number(yaw, -180, 180, 'yaw')
    pose = {'x': x, 'y': y, 'z': height, 'yaw': yaw}
    # The executor checks these bounds again before it moves. Checking them here too
    # names the axis while the caller still knows which detection it came from.
    bounds = config.get('workspace_mm') or {}
    for axis in ('x', 'y', 'z'):
        pair = bounds.get(axis)
        if isinstance(pair, list) and len(pair) == 2 and not pair[0] <= pose[axis] <= pair[1]:
            raise ValueError(f'{axis}={pose[axis]:.1f} mm is outside the calibrated '
                             f'workspace {pair}; the arm cannot be sent there')
    return pose


def target_profile(config, object_id):
    """Offline admission of human-measured geometry; no credentials or robot needed."""
    if object_id not in config['objects']:
        raise ValueError(f'Unknown localization object: {object_id}')
    path = settings(config)['target_profiles'].get(object_id)
    if not path:
        raise ValueError(f'{object_id}: missing measured localization target profile')
    path = ROOT / path
    try:
        profile = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f'{object_id}: cannot read localization target profile') from exc
    required = {'schema', 'status', 'object_id', 'view', 'camera_resource', 'frame', 'model', 'detector_version',
                'calibration_sha256', 'image_size', 'bbox_anchor', 'offset_xy_mm', 'z_mm',
                'orientation', 'max_error_mm', 'tolerance_mm', 'validated_region_mm',
                'scope', 'evidence'}
    if not isinstance(profile, dict) or set(profile) != required:
        raise ValueError(f'{object_id}: target profile has missing or unexpected fields')
    if (profile['schema'] != 'localization-profile/1' or profile['status'] != 'validated'
            or profile['object_id'] != object_id or profile['frame'] != config['frame']
            or profile['scope'] != 'fixed_orientation_and_height'
            or profile['detector_version'] != 'bbox-1000-v1'):
        raise ValueError(f'{object_id}: needs a validated profile for this object, frame and detector')
    if not isinstance(profile['view'], str):
        raise ValueError('Profile view must name a configured camera')
    measured = calibration(config, profile['view'])
    if (measured['frame'] != config['frame'] or measured['units'] != 'mm'
            or profile['camera_resource'] != camera.resolve(config, profile['view'])):
        raise ValueError('Profile/calibration must match the current camera resource, frame and millimetre units')
    if not measured['image_size'] or profile['image_size'] != measured['image_size']:
        raise ValueError('Calibration/profile needs matching measured image_size; do not guess resolution')
    if profile['calibration_sha256'] != measured['sha256']:
        raise ValueError('Calibration changed since the object profile was validated')
    if profile['model'] != vision.credentials()['OPENROUTER_VISION_MODEL']:
        raise ValueError('Vision model differs from the validated localization profile')
    for key, low, high in (('bbox_anchor', 0, 1), ('offset_xy_mm', -10000, 10000)):
        if not isinstance(profile[key], list) or len(profile[key]) != 2:
            raise ValueError(f'{key} must contain two measured values')
        for value in profile[key]:
            number(value, low, high, key)
    for key in ('max_error_mm', 'tolerance_mm'):
        number(profile[key], 0 if key == 'max_error_mm' else 0.001, 10000, key)
    error = measured['accuracy_mm'].get('max_error_mm')
    if error is None or max(error, profile['max_error_mm']) > profile['tolerance_mm']:
        raise ValueError('Measured calibration/localization error exceeds the approved grasp tolerance')
    number(profile['z_mm'], -10000, 10000, 'z_mm')
    orientation = profile['orientation']
    if not isinstance(orientation, dict) or set(orientation) != {'o_x', 'o_y', 'o_z', 'theta'}:
        raise ValueError('Profile needs a measured Viam orientation')
    for key, value in orientation.items():
        number(value, -360 if key == 'theta' else -1, 360 if key == 'theta' else 1, key)
    if sum(orientation[key] ** 2 for key in ('o_x', 'o_y', 'o_z')) < 1e-8:
        raise ValueError('Profile orientation cannot be zero')
    region = profile['validated_region_mm']
    if not isinstance(region, dict) or set(region) != {'x', 'y'}:
        raise ValueError('Profile needs a measured XY validation region')
    for axis, pair in region.items():
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Profile validation region must contain two bounds per axis')
        for value in pair:
            number(value, -10000, 10000, axis)
        if pair[0] >= pair[1]:
            raise ValueError('Profile validation region bounds must increase')
    evidence = profile['evidence']
    if not isinstance(evidence, str) or not evidence.strip() or not (ROOT / evidence).is_file():
        raise ValueError('Profile must reference existing physical measurement evidence')
    return profile


def readiness(config):
    """Configuration status only; this never claims the current scene is ready."""
    targets = {}
    for identity in config['objects']:
        try:
            target_profile(config, identity)
            targets[identity] = {'configured': True, 'reason': 'Profile checks pass; fresh scene and outcome gates still required'}
        except ValueError as exc:
            targets[identity] = {'configured': False, 'reason': str(exc)}
    calibrations = {}
    try:
        for view in settings(config)['homographies']:
            measured = calibration(config, view)
            calibrations[view] = {k: measured[k] for k in ('image_size', 'accuracy_mm')}
    except ValueError as exc:
        calibrations['error'] = str(exc)
    return {'targets': targets, 'calibrations': calibrations}


def target_from_observation(config, object_id, observation, *, view, captured_at):
    """Project a validated detector anchor. Fixed height/orientation are preconditions."""
    profile = target_profile(config, object_id)
    if observation.get('model') != profile['model'] or observation.get('detector_version') != profile['detector_version']:
        raise ValueError('Observation does not match the validated detector')
    if view != profile['view']:
        raise ValueError('Observation is from the wrong camera view')
    info = observation.get('image', {})
    if [info.get('width'), info.get('height')] != profile['image_size']:
        raise ValueError('Image resolution differs from the measured calibration')
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(captured_at)).total_seconds()
    except (ValueError, TypeError) as exc:
        raise ValueError('Capture timestamp is unknown') from exc
    if not 0 <= age <= settings(config)['max_observation_age_s']:
        raise ValueError('Observation is stale or its timestamp is in the future')
    checked = vision.validate_observation({k: observation.get(k) for k in ('summary', 'detections')}, config['objects'])
    matches = [item for item in checked['detections'] if item['object_id'] == object_id]
    if len(matches) != 1 or matches[0]['visibility'] != 'clear':
        raise ValueError(f'{object_id}: absent, occluded or uncertain; no target produced')
    left, top, right, bottom = matches[0]['bbox']
    u, v = profile['bbox_anchor']
    px = (left + u * (right - left)) * info['width']
    py = (top + v * (bottom - top)) * info['height']
    mapped = pixel_to_frame(config, view, px, py)
    x, y = mapped['x'] + profile['offset_xy_mm'][0], mapped['y'] + profile['offset_xy_mm'][1]
    for axis, value in (('x', x), ('y', y)):
        if not profile['validated_region_mm'][axis][0] <= value <= profile['validated_region_mm'][axis][1]:
            raise ValueError('Detected target lies outside the physically validated region')
    pose = {'x': x, 'y': y, 'z': profile['z_mm'], **profile['orientation']}
    if not config['calibrated']:
        raise ValueError('Localization requires calibrated workspace bounds')
    for axis in ('x', 'y', 'z'):
        bounds = config['workspace_mm'][axis]
        if not bounds[0] <= pose[axis] <= bounds[1]:
            raise ValueError('Localized target lies outside the calibrated workspace')
    # Preserve the registry's exact Target contract. Evidence is saved separately.
    return {'object_id': object_id, 'frame': config['frame'], 'pose': pose}


async def localize(ctx: Context, *, object_id: str) -> Target:
    """OpenRouter detection + measured planar object profile -> fresh manipulation Target.

    Requires a stationary scene and the profile's fixed object orientation/height,
    established by the operator's entry gate. Never moves the arm.
    """
    profile = target_profile(ctx.config, object_id)
    captured = await camera.capture(ctx, view=profile['view'])
    images = [item for item in captured['images'] if item['mime_type'] in ('image/jpeg', 'image/png', 'image/webp')]
    if len(images) != 1:
        raise ValueError('Localization needs exactly one unambiguous colour image per camera view')
    path = ROOT / images[0]['path']
    observation = await asyncio.to_thread(vision.observe, path.read_bytes(), [object_id], model=profile['model'])
    record = {'object_id': object_id, 'view': captured['view'], 'captured_at': captured['captured_at'],
              'image_path': str(path), 'observation': observation}
    try:
        target = target_from_observation(ctx.config, object_id, observation,
                                         view=captured['view'], captured_at=captured['captured_at'])
        record['target'] = target
        return target
    except ValueError as exc:
        record['blocked'] = str(exc)
        raise
    finally:
        path.with_suffix('.localization.json').write_text(json.dumps(record, indent=2, allow_nan=False) + '\n')
