"""Camera team: photograph the station. These primitives observe; they never move.

One capture per call, from a view the task config names -- `wrist` for the camera on
the arm, `overhead` for the one above the station. Plans ask for a view, never for a
Viam resource name, so a plan cannot reach a camera the station has not declared.

A run log has to stay JSON, so the image itself does not come back through the
result. The bytes are written to `primitive_settings.camera.image_dir` and the
result carries the path, the mime type, the size, and the camera's own capture
time, which is the evidence a gate or a later review can actually check.
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .types import Context

IMPLEMENTED = {'capture'}

ROOT = Path(__file__).resolve().parents[1]
SAFE_NAME = re.compile(r'[^A-Za-z0-9_-]+')
EXTENSIONS = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/vnd.viam.rgba': '.rgba',
              'image/vnd.viam.dep': '.dep'}
DEFAULT_SETTINGS = {
    'views': None,               # None means the one camera in resources, as the wrist view.
    'image_dir': 'runs/images',  # Relative paths hang off the repository root, which Git ignores.
}


def settings(config):
    """Validate the team-owned primitive_settings.camera block over the defaults."""
    supplied = config.get('primitive_settings', {}).get('camera', {})
    if not isinstance(supplied, dict) or not set(supplied) <= set(DEFAULT_SETTINGS):
        raise ValueError('primitive_settings.camera accepts only: '
                         + ', '.join(sorted(DEFAULT_SETTINGS)))
    values = {**DEFAULT_SETTINGS, **supplied}
    named = values['views']
    if named is None:
        named = {'wrist': config['resources']['camera']}
    if not isinstance(named, dict) or not named:
        raise ValueError('primitive_settings.camera.views must name at least one view, '
                         'for example {"wrist": "cam", "overhead": "overhead-cam"}')
    for view, resource in named.items():
        if not isinstance(view, str) or not view.isidentifier():
            raise ValueError(f'Camera view name {view!r} must be an identifier')
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError(f'primitive_settings.camera.views.{view} must name a camera '
                             'resource on the machine')
    if not isinstance(values['image_dir'], str) or not values['image_dir'].strip():
        raise ValueError('primitive_settings.camera.image_dir must be a directory path')
    return {**values, 'views': dict(named)}


def views(config):
    """View name -> camera resource. The planner sees these names and nothing else."""
    return settings(config)['views']


def resolve(config, view):
    named = views(config)
    if view not in named:
        raise ValueError(f'Unknown camera view {view!r}; this station has: '
                         + ', '.join(sorted(named)))
    return named[view]


def image_dir(config):
    directory = Path(settings(config)['image_dir'])
    return directory if directory.is_absolute() else ROOT / directory


def kind_of(image):
    """The mime type as a plain string. The SDK marks lazily encoded images."""
    return str(image.mime_type).removesuffix('+lazy')


def filename(view, image, index, stamp):
    """One file per returned imager, named so a run's images sort and never collide."""
    source = SAFE_NAME.sub('-', getattr(image, 'name', '') or str(index)).strip('-')
    return (f'{stamp.strftime("%Y%m%dT%H%M%S")}-{uuid4().hex[:8]}-{view}-'
            f'{source or index}{EXTENSIONS.get(kind_of(image), ".bin")}')


def dimensions(image, data):
    """Pixel size. The SDK leaves it unset for Viam depth, whose header carries it."""
    if image.width is None and kind_of(image) == 'image/vnd.viam.dep' and len(data) >= 24:
        return int.from_bytes(data[8:16], 'big'), int.from_bytes(data[16:24], 'big')
    return image.width, image.height


def shown(path):
    """Files inside the repository are logged relative to it; anywhere else, absolute."""
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def captured_at(metadata):
    """The camera's own capture time, when it reports one."""
    stamp = getattr(metadata, 'captured_at', None)
    if stamp is not None and (stamp.seconds or stamp.nanos):
        return stamp.ToDatetime(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


async def capture(ctx: Context, *, view: str) -> dict:
    """Photograph the station from a configured view; move nothing.

    Postcondition: nothing moved, and one file per imager the camera returned is on
    disk. The result reports every file with its mime type, pixel size, byte count,
    and the camera's capture time. A camera that pairs colour with depth returns
    both, so `images` is a list. An empty response is a failure, not an empty result:
    this primitive proves a picture exists, never what is in it.
    """
    from viam.components.camera import Camera

    if ctx.robot is None:
        raise ValueError('capture needs a connected robot')
    resource = resolve(ctx.config, view)
    timeout = ctx.config['limits']['primitive_timeout_s']
    camera = Camera.from_robot(ctx.robot, resource)
    images, metadata = await camera.get_images(timeout=timeout)
    if not images:
        raise RuntimeError(f'Camera {resource!r} returned no image for the {view} view')
    stamp = captured_at(metadata)
    directory = image_dir(ctx.config)
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, image in enumerate(images):
        data = image.data
        if not data:
            raise RuntimeError(f'Camera {resource!r} returned an empty image for the '
                               f'{view} view')
        path = directory / filename(view, image, index, stamp)
        path.write_bytes(data)
        width, height = dimensions(image, data)
        saved.append({'name': getattr(image, 'name', '') or None, 'path': shown(path),
                      'mime_type': kind_of(image), 'bytes': len(data),
                      'width': width, 'height': height})
    return {'view': view, 'camera': resource, 'captured_at': stamp.isoformat(),
            'images': saved}
