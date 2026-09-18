"""Perception team: camera/detection/frame transforms belong here.

Detection is OpenCV's job and stays outside this module; what lives here is the one
step after it -- turning a pixel a detector found into a place the arm can be sent.

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
from pathlib import Path

from . import camera
from .types import Context, PoseYaw, Target

IMPLEMENTED = set()

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = {
    'homographies': {},  # Camera view name -> calibration artifact path. No view, no mapping.
    'hover_z_mm': None,  # Task-frame z for a move over a detection. Deliberately unset.
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
    return {**values, 'homographies': dict(named)}


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
    return {'H': matrix(raw['H'], name), 'view': view, 'source': named[view],
            'accuracy_mm': accuracy}


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


async def localize(ctx: Context, *, object_id: str) -> Target:
    """Return the object's manipulation target in ctx.config['frame']; fail if uncertain."""
    raise NotImplementedError('Team supplies localization')
