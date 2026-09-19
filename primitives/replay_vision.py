"""Local image translation for taught grasps; no robot I/O or global homography.

Two textured patches must agree on translation. A measured local 2x2 map converts
pixels to task-frame XY. Fixed camera, object height/orientation and cup placement
remain operator preconditions. This is a separate adapter, not generic localize.
"""
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from . import camera, vision
from .localization import number

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'replay-vision/1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profiles(config):
    block = config.get('primitive_settings', {}).get('replay_vision', {})
    if not isinstance(block, dict) or set(block) - {'profiles'}:
        raise ValueError('replay_vision accepts only profiles')
    entries = block.get('profiles', {})
    if not isinstance(entries, dict) or any(k not in config['objects'] or not isinstance(v, str)
                                          or not v.strip() for k, v in entries.items()):
        raise ValueError('replay_vision.profiles must map configured objects to profile files')
    return entries


def image(path):
    data = Path(path).read_bytes()
    info = vision.image_info(data)
    return Image.open(io.BytesIO(data)).convert('L'), [info['width'], info['height']]


def reference(episode):
    root, meta, _ = episode
    images = meta['observations']['before']['frames']['overhead']['images']
    rgb = [im for im in images if im.get('mime_type') in ('image/jpeg', 'image/png', 'image/webp')]
    if len(rgb) != 1:
        raise ValueError('Vision replay needs one overhead colour reference image')
    path = (Path(root) / rgb[0]['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Reference image must remain inside episode')
    if rgb[0].get('sha256') and digest(path) != rgb[0]['sha256']:
        raise ValueError('Teaching reference image changed')
    return path


def patches_valid(patches, size, radius):
    if type(radius) is not int or not 4 <= radius <= 64:
        raise ValueError('search_px must be an integer from 4 to 64')
    if not isinstance(patches, list) or len(patches) != 2:
        raise ValueError('Choose exactly two separated texture patches on the same object')
    for box in patches:
        if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
            raise ValueError('Patch boxes must be integer [left, top, right, bottom] pixels')
        l, t, r, b = box
        if not (12 <= r-l <= 96 and 12 <= b-t <= 96 and
                radius <= l < r <= size[0]-radius and radius <= t < b <= size[1]-radius):
            raise ValueError('Patches need texture area 12..96 px and search clearance inside image')
    a, b = patches
    if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
        raise ValueError('Texture patches must not overlap')


def match(reference_path, current_path, patches, radius):
    """Normalized correlation, rejecting weak, ambiguous or inconsistent matches."""
    ref, size = image(reference_path)
    current, current_size = image(current_path)
    if current_size != size:
        raise ValueError('Camera image resolution changed')
    patches_valid(patches, size, radius)
    results = []
    for box in patches:
        template = ref.crop(box)
        stats = ImageStat.Stat(template)
        mean, std = stats.mean[0], stats.stddev[0]
        if std < 12:
            raise ValueError('Reference patch has too little texture')
        scores = []
        for dy in range(-radius, radius+1):
            for dx in range(-radius, radius+1):
                crop = current.crop((box[0]+dx, box[1]+dy, box[2]+dx, box[3]+dy))
                cs = ImageStat.Stat(crop)
                if cs.stddev[0] < 12:
                    continue
                mse = ImageStat.Stat(ImageChops.difference(template, crop)).sum2[0] / (template.width*template.height)
                score = (std*std + cs.stddev[0]**2 + (mean-cs.mean[0])**2-mse)/(2*std*cs.stddev[0])
                scores.append((score, dx, dy))
        if not scores:
            raise ValueError('Object patch is absent or unrecognizable')
        score, dx, dy = max(scores)
        runner = max((s for s, x, y in scores if math.hypot(x-dx, y-dy) > 3), default=-1)
        if score < .92 or score-runner < .03 or max(abs(dx), abs(dy)) == radius:
            raise ValueError('Object patch is weak, ambiguous, occluded or outside search area')
        results.append(dict(dx=dx, dy=dy, score=score, margin=score-runner))
    if math.dist([results[0]['dx'], results[0]['dy']], [results[1]['dx'], results[1]['dy']]) > 1.5:
        raise ValueError('Object patches disagree: possible rotation, occlusion or wrong object')
    return dict(pixel_shift=[sum(r[k] for r in results)/2 for k in ('dx', 'dy')], patches=results,
                image_size=size)


def hull(points):
    points = sorted(set(tuple(p) for p in points))
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    parts = []
    for sequence in (points, list(reversed(points))):
        half = []
        for p in sequence:
            while len(half) >= 2 and cross(half[-2], half[-1], p) <= 0:
                half.pop()
            half.append(p)
        parts.append(half[:-1])
    return parts[0]+parts[1]


def inside(point, polygon):
    return len(polygon) >= 3 and all((b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0]) >= -1e-8
                                   for a, b in zip(polygon, polygon[1:]+polygon[:1]))


def mapped(matrix, pixel):
    return [sum(a*b for a, b in zip(row, pixel)) for row in matrix]


def load_profile(config, skill, episode):
    entries = profiles(config)
    if skill not in entries:
        raise ValueError(f'{skill}: missing measured replay_vision profile')
    path = ROOT / entries[skill]
    p = json.loads(path.read_text())
    if (p.get('schema') != SCHEMA or p.get('object_id') != skill or p.get('frame') != config['frame']
            or p.get('camera_resource') != camera.resolve(config, 'overhead')
            or p.get('scope') != 'fixed_camera_orientation_height_and_cup'):
        raise ValueError('Vision profile does not match this object/station/scope')
    if p['episode_sha256'] != digest(Path(episode[0])/'metadata.json') or p['reference_sha256'] != digest(reference(episode)):
        raise ValueError('Vision profile does not match the taught episode/reference')
    _, size = image(reference(episode))
    if p['image_size'] != size:
        raise ValueError('Vision profile resolution differs from reference')
    patches_valid(p['patches'], size, p['search_px'])
    number(p['tolerance_mm'], .001, 20, 'tolerance_mm')
    number(p['max_error_mm'], 0, p['tolerance_mm'], 'max_error_mm')
    number(p['max_offset_mm'], .001, 20, 'max_offset_mm')
    matrix = p['xy_mm_per_pixel']
    if not isinstance(matrix, list) or len(matrix) != 2 or any(not isinstance(r, list) or len(r) != 2 for r in matrix):
        raise ValueError('Vision profile needs a measured 2x2 map')
    for row in matrix:
        for v in row:
            number(v, -100, 100, 'xy_mm_per_pixel')
    if abs(matrix[0][0]*matrix[1][1]-matrix[0][1]*matrix[1][0]) < 1e-8:
        raise ValueError('Vision profile map is singular')
    polygon = p['validated_polygon_mm']
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError('Vision profile needs a measured validation region')
    for xy in polygon:
        if not isinstance(xy, list) or len(xy) != 2:
            raise ValueError('Invalid validation region')
        for v in xy:
            number(v, -20, 20, 'validation region')
    if hull(polygon) != [tuple(xy) for xy in polygon] or not inside([0, 0], polygon):
        raise ValueError('Validation region must be convex and include taught origin')
    evidence = p['evidence_sha256']
    if not isinstance(evidence, dict) or len(evidence) < 9:
        raise ValueError('Vision profile needs fit, independent checks and rejection evidence')
    for file, sha in evidence.items():
        if digest(ROOT / file) != sha:
            raise ValueError('Vision measurement evidence changed')
    return p


def offset(config, skill, episode, current_path, captured_at):
    p = load_profile(config, skill, episode)
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(captured_at)).total_seconds()
    except (TypeError, ValueError) as exc:
        raise ValueError('Unknown image capture time') from exc
    if not 0 <= age <= 30:
        raise ValueError('Vision image is stale or in the future')
    evidence = match(reference(episode), current_path, p['patches'], p['search_px'])
    xy = mapped(p['xy_mm_per_pixel'], evidence['pixel_shift'])
    if math.hypot(*xy) > p['max_offset_mm'] or not inside(xy, p['validated_polygon_mm']):
        raise ValueError('Vision offset is outside the measured demo region')
    return dict(dx=xy[0], dy=xy[1], dz=0), dict(**evidence, profile_sha256=digest(ROOT/profiles(config)[skill]),
                                             current_sha256=digest(current_path), captured_at=captured_at)


def build_profile(config, skill, episode, measurements_path, out):
    """Fit measured displacements and independently check the full image-to-XY path."""
    measurements_path, out = Path(measurements_path).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Use a new profile path; preserve earlier measurements')
    data = json.loads(measurements_path.read_text())
    if data.get('measurement_source') != 'operator_measured_task_frame_mm':
        raise ValueError('Offsets must be operator-measured in task-frame millimetres')
    tolerance, limit = data['tolerance_mm'], data.get('max_offset_mm', 20)
    number(tolerance, .001, 20, 'team-approved tolerance_mm')
    number(limit, .001, 20, 'max_offset_mm')
    radius, patches = data.get('search_px', 48), data['patches']
    ref = reference(episode)
    dependencies = {str(measurements_path): digest(measurements_path)}
    seen = {digest(ref)}
    groups = {}
    if len(data['fit']) < 2 or len(data['check']) < 4:
        raise ValueError('Need >=2 independent fit placements and >=4 held-out checks around origin')
    for group in ('fit', 'check', 'reject'):
        rows = []
        for sample in data[group]:
            path = (measurements_path.parent/sample['image']).resolve()
            sha = digest(path)
            if sha in seen:
                raise ValueError('Fit/check/rejection images must be distinct fresh captures')
            seen.add(sha)
            dependencies[str(path)] = sha
            if group == 'reject':
                if image(path)[1] != image(ref)[1]:
                    raise ValueError('Negative examples must use the same camera resolution')
                try:
                    match(ref, path, patches, radius)
                except ValueError:
                    rows.append(sample['reason'])
                else:
                    raise ValueError('Negative image was accepted; choose better patches/setup')
                continue
            xy = sample['offset_mm']
            if not isinstance(xy, list) or len(xy) != 2:
                raise ValueError('Measured offset_mm must be [dx, dy]')
            for v in xy:
                number(v, -20, 20, 'measured offset')
            if math.hypot(*xy) > limit:
                raise ValueError('Measurement exceeds bounded demo offset')
            found = match(ref, path, patches, radius)
            rows.append((found['pixel_shift'], xy))
        groups[group] = rows
    if not {'absent', 'occluded'} <= set(groups['reject']):
        raise ValueError('Need rejected absent and occluded object images')
    xx = sum(p[0]**2 for p, _ in groups['fit'])
    yy = sum(p[1]**2 for p, _ in groups['fit'])
    xy = sum(p[0]*p[1] for p, _ in groups['fit'])
    det = xx*yy-xy*xy
    if xx*yy == 0 or det/(xx*yy) < .05:
        raise ValueError('Fit placements must span two independent image directions')
    matrix = []
    for axis in (0, 1):
        xz = sum(p[0]*q[axis] for p, q in groups['fit'])
        yz = sum(p[1]*q[axis] for p, q in groups['fit'])
        matrix.append([(yy*xz-xy*yz)/det, (xx*yz-xy*xz)/det])
    error = max(math.dist(mapped(matrix, p), q) for p, q in groups['fit']+groups['check'])
    if error > tolerance:
        raise ValueError(f'Measured image-to-XY error {error:.3f} mm exceeds tolerance {tolerance:g} mm')
    polygon = hull([q for _, q in groups['check']])
    if not inside([0, 0], polygon):
        raise ValueError('Independent check placements must surround the taught origin')
    _, size = image(ref)
    p = dict(schema=SCHEMA, object_id=skill, frame=config['frame'], camera_resource=camera.resolve(config, 'overhead'),
             scope='fixed_camera_orientation_height_and_cup', episode_sha256=digest(Path(episode[0])/'metadata.json'),
             reference_sha256=digest(ref), image_size=size, patches=patches, search_px=radius,
             xy_mm_per_pixel=matrix, tolerance_mm=tolerance, max_error_mm=error, max_offset_mm=limit,
             validated_polygon_mm=polygon, evidence_sha256=dependencies,
             status='measured_candidate_requires_physical_rehearsal')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(p, indent=2, allow_nan=False)+'\n')
    return p
