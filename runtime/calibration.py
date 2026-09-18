"""Derive the task workspace from the machine's own configured collision geometry.

`workspace_mm` is the envelope the executor checks poses against. It is not a second
copy of the machine's geometry: it is computed from it. Every bound comes from a
frame the machine already publishes -- the table, the ceiling, the walls -- shrunk
by the configured tool's own geometry and a declared clearance, so a pose inside the
box keeps the whole tool clear of every configured obstacle. Faces that no obstacle
bounds fall back to a declared reach cap, which is a policy number, not a promise
that every pose inside it is reachable; the motion service still plans every move.

Read-only: nothing here moves the arm or writes a file. The caller supplies the
frame system as plain dicts, so the whole derivation is testable without a machine.
"""
import math

AXES = ('x', 'y', 'z')
POSE_FIELDS = ('x', 'y', 'z', 'o_x', 'o_y', 'o_z', 'theta')
IDENTITY = (((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0))
# Clearance kept from configured geometry on top of the tool's own extent. Small by
# default: the machine's obstacles are already where the machine says they are.
DEFAULT_SETTINGS = {'margin_mm': 10.0, 'reach_mm': None}


def number(value, low, high, name):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not low <= value <= high):
        raise ValueError(f'{name} must be a finite number in [{low}, {high}]')


def settings(config):
    """Validate the optional calibration block. `derived` is provenance we wrote back."""
    supplied = config.get('calibration', {})
    if not isinstance(supplied, dict) or not set(supplied) <= {*DEFAULT_SETTINGS, 'derived'}:
        raise ValueError('calibration accepts only: '
                         + ', '.join((*sorted(DEFAULT_SETTINGS), 'derived')))
    values = {**DEFAULT_SETTINGS,
              **{key: supplied[key] for key in DEFAULT_SETTINGS if key in supplied}}
    number(values['margin_mm'], 0, 500, 'calibration.margin_mm')
    if values['reach_mm'] is not None:
        number(values['reach_mm'], 1, 10000, 'calibration.reach_mm')
    return values


# --- Frame system geometry ----------------------------------------------------
# A placement is (rotation rows, translation). Frame poses arrive as Viam orientation
# vectors; a kinematics model states its own as a quaternion or an orientation vector.

def pose_of(raw):
    """A frame-system pose as plain numbers. Protobuf drops zeros; a zero OV is identity."""
    pose = {key: float(raw.get(key, 0.0)) for key in POSE_FIELDS}
    if sum(pose[key] ** 2 for key in ('o_x', 'o_y', 'o_z')) < 1e-12:
        pose['o_z'] = 1.0
    return pose


def rows_of_quaternion(w, x, y, z, name):
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-12:
        raise ValueError(f'{name}: zero-length orientation quaternion')
    w, x, y, z = (value / norm for value in (w, x, y, z))
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def placement_of_pose(pose, name):
    """Placement of a frame given its Viam pose. The SDK owns the OV convention."""
    from viam.spatialmath import OrientationVector

    turn = OrientationVector(o_x=pose['o_x'], o_y=pose['o_y'], o_z=pose['o_z'],
                             theta=math.radians(pose['theta'])).to_quaternion()
    return (rows_of_quaternion(turn.w, turn.i, turn.j, turn.k, name),
            (pose['x'], pose['y'], pose['z']))


def rows_of_model_orientation(turn, name):
    """A kinematics model states orientation as a quaternion or an orientation vector."""
    kind, value = turn.get('type') or '', turn.get('value') or {}
    if not kind or not value:
        return IDENTITY[0]
    if kind == 'quaternion':
        return rows_of_quaternion(*(float(value.get(key, 0.0)) for key in ('W', 'X', 'Y', 'Z')),
                                  name)
    if kind in ('ov_degrees', 'ov_radians'):
        angle = float(value.get('th', 0.0))
        return placement_of_pose({'x': 0.0, 'y': 0.0, 'z': 0.0,
                                  **{f'o_{key}': float(value.get(key, 0.0)) for key in AXES},
                                  'theta': angle if kind == 'ov_degrees' else math.degrees(angle)},
                                 name)[0]
    raise ValueError(f'{name}: the machine model states orientation as {kind!r}; calibration '
                     'will not guess where that geometry points. Read it with inspect and set '
                     'that bound by hand.')


def placement_of_model(raw, name):
    """Placement of a link or geometry inside a kinematics model."""
    offset = raw.get('translation') or {}
    shift = tuple(float(offset.get(key, offset.get(key.lower(), 0.0))) for key in ('X', 'Y', 'Z'))
    return rows_of_model_orientation(raw.get('orientation') or {}, name), shift


def combine(outer, inner):
    rows, shift = outer
    inner_rows, inner_shift = inner
    return (tuple(tuple(sum(rows[i][k] * inner_rows[k][j] for k in range(3)) for j in range(3))
                  for i in range(3)),
            tuple(shift[i] + sum(rows[i][k] * inner_shift[k] for k in range(3)) for i in range(3)))


def at(placement, point):
    rows, shift = placement
    return tuple(shift[i] + sum(rows[i][k] * point[k] for k in range(3)) for i in range(3))


def half_extents(geometry, name):
    """Half extents of a model geometry. A sphere or capsule contributes its own box."""
    size = tuple(float(geometry.get(key, 0.0)) for key in ('x', 'y', 'z'))
    radius, length = float(geometry.get('r', 0.0)), float(geometry.get('l', 0.0))
    kind = geometry.get('type') or ''
    if kind == 'box' or (not kind and all(value > 0 for value in size)):
        return tuple(value / 2 for value in size)
    if kind == 'capsule' or (not kind and radius > 0 and length > 0):
        return (radius, radius, length / 2)
    if kind == 'sphere' or (not kind and radius > 0):
        return (radius, radius, radius)
    raise ValueError(f'{name}: unsupported configured geometry {geometry!r}')


def object_shape(physical, name):
    """A geometry attached straight to a frame, as the robot API reports it."""
    if 'box' in physical:
        dims = physical['box'].get('dims_mm', {})
        half = tuple(float(dims.get(key, 0.0)) / 2 for key in AXES)
    elif 'sphere' in physical:
        radius = float(physical['sphere'].get('radius_mm', 0.0))
        half = (radius,) * 3
    elif 'capsule' in physical:
        radius = float(physical['capsule'].get('radius_mm', 0.0))
        half = (radius, radius, float(physical['capsule'].get('length_mm', 0.0)) / 2)
    else:
        raise ValueError(f'{name}: unsupported configured geometry {physical!r}')
    return placement_of_pose(pose_of(physical.get('center', {})), name), half


def model_shapes(kinematics, name):
    """Every geometry in a kinematics model, placed in that frame's own coordinates."""
    links = [link for link in kinematics.get('links', []) if isinstance(link, dict)]
    by_id = {link.get('id'): link for link in links}
    shapes = []
    for link in links:
        geometry = link.get('geometry')
        if not geometry:
            continue
        chain, seen, node = [], set(), link
        while node is not None and node.get('id') not in seen:
            seen.add(node.get('id'))
            chain.append(node)
            node = by_id.get(node.get('parent'))
        placement = IDENTITY
        for node in reversed(chain):
            placement = combine(placement, placement_of_model(node, name))
        shapes.append((combine(placement, placement_of_model(geometry, name)),
                       half_extents(geometry, name)))
    return shapes


def frame_shapes(entry, name):
    """Geometry of one frame system entry, in that frame's own coordinates."""
    shapes = model_shapes(entry.get('kinematics') or {}, name)
    physical = (entry.get('frame') or {}).get('physical_object')
    if physical:
        shapes.append(object_shape(physical, name))
    return shapes


def box_of(shapes):
    """Axis-aligned bounds of placed shapes: every corner, so rotations are honoured."""
    low = [math.inf] * 3
    high = [-math.inf] * 3
    for placement, half in shapes:
        for step in range(8):
            corner = at(placement, tuple(half[i] * (1 if step >> i & 1 else -1) for i in range(3)))
            for i in range(3):
                low[i], high[i] = min(low[i], corner[i]), max(high[i], corner[i])
    return tuple(low), tuple(high)


# --- What the frame system means for this task --------------------------------

def entry_named(frames, name):
    return next((entry for entry in frames
                 if (entry.get('frame') or {}).get('reference_frame') == name), None)


def obstacles(frames, config):
    """Static geometry in the task frame, as world boxes, plus what was left out.

    Only frames bolted straight to the task frame with no joints count: anything
    that moves is the arm's or the planner's problem, not a fixed workspace bound.
    """
    roles = set(config['resources'].values())
    world = config['frame']
    found, skipped = [], []
    for entry in frames:
        frame = entry.get('frame') or {}
        name = frame.get('reference_frame')
        parent = (frame.get('pose_in_observer_frame') or {}).get('reference_frame')
        if name in roles:
            skipped.append({'frame': name, 'reason': 'configured task resource'})
            continue
        if parent != world:
            skipped.append({'frame': name, 'reason': f'attached to {parent!r}, not {world!r}'})
            continue
        if (entry.get('kinematics') or {}).get('joints'):
            skipped.append({'frame': name, 'reason': 'articulated model, not fixed geometry'})
            continue
        shapes = frame_shapes(entry, name)
        if not shapes:
            skipped.append({'frame': name, 'reason': 'no configured geometry'})
            continue
        placement = placement_of_pose(
            pose_of((frame.get('pose_in_observer_frame') or {}).get('pose', {})), name)
        low, high = box_of([(combine(placement, shape), half) for shape, half in shapes])
        found.append({'name': name, 'min': low, 'max': high})
    return found, skipped


def tool_envelope(frames, config):
    """How far the configured tool reaches past the commanded point, from its geometry.

    go_to_pose points the tool down, so the tool frame's +Z is world -Z: geometry at
    negative local Z sits above the commanded point, positive local Z below it. Yaw
    is free, so the sideways extent is a radius.
    """
    name = config['resources']['gripper']
    entry = entry_named(frames, name)
    if entry is None:
        raise ValueError(f'No frame for the configured gripper {name!r}: calibration needs '
                         'its geometry to know how much room the tool takes')
    shapes = frame_shapes(entry, name)
    if not shapes:
        raise ValueError(f'The configured gripper {name!r} has no geometry on the machine; '
                         'add it to the machine config so the tool can be kept clear')
    low, high = box_of(shapes)
    return {'above_mm': max(0.0, -low[2]), 'below_mm': max(0.0, high[2]),
            'radius_mm': math.hypot(max(abs(low[0]), abs(high[0])),
                                    max(abs(low[1]), abs(high[1])))}


def arm_base(frames, config):
    """Where the arm is bolted, in the task frame."""
    name = config['resources']['arm']
    entry = entry_named(frames, name)
    if entry is None:
        raise ValueError(f'No frame for the configured arm {name!r}; run inspect')
    held = (entry.get('frame') or {}).get('pose_in_observer_frame') or {}
    if held.get('reference_frame') != config['frame']:
        raise ValueError(f'The arm {name!r} is attached to '
                         f'{held.get("reference_frame")!r}, not {config["frame"]!r}: '
                         'calibration cannot place the reach cap')
    pose = pose_of(held.get('pose', {}))
    return tuple(pose[axis] for axis in AXES)


def kinematic_reach(frames, config):
    """Upper bound on tool distance from the arm base: every link offset, fully extended."""
    arm = entry_named(frames, config['resources']['arm'])
    links = (arm.get('kinematics') or {}).get('links', []) if arm else []
    total = sum(math.dist((0.0, 0.0, 0.0), placement_of_model(link, 'arm')[1]) for link in links)
    tool = entry_named(frames, config['resources']['gripper'])
    if tool is not None:
        pose = pose_of(((tool.get('frame') or {}).get('pose_in_observer_frame') or {}).get('pose', {}))
        total += math.dist((0.0, 0.0, 0.0), tuple(pose[axis] for axis in AXES))
    if total <= 0:
        raise ValueError('The arm publishes no link geometry: set calibration.reach_mm')
    return total


# --- Free box -----------------------------------------------------------------

def inflated(obstacle, tool, margin):
    """The obstacle grown by the room the tool needs: keep the commanded point out of this."""
    side = tool['radius_mm'] + margin
    low, high = list(obstacle['min']), list(obstacle['max'])
    low[0] -= side
    high[0] += side
    low[1] -= side
    high[1] += side
    low[2] -= tool['above_mm'] + margin   # Tool body reaches up: stay this far below.
    high[2] += tool['below_mm'] + margin  # Claws reach down: stay this far above.
    return tuple(low), tuple(high)


def overlaps(box, low, high):
    return all(low[i] < box[axis][1] and high[i] > box[axis][0] for i, axis in enumerate(AXES))


def volume(box):
    return math.prod(max(0.0, high - low) for low, high in box.values())


def free_box(envelope, anchor, blocked):
    """Largest axis-aligned box inside the envelope that holds the anchor and no obstacle.

    Each cut is a whole face moved to an obstacle's edge, which removes that obstacle
    outright, so this ends after at most one cut per obstacle. Ties go to the cut that
    keeps the most room.
    """
    box = {axis: list(envelope[axis]) for axis in AXES}
    source = {axis: ['reach', 'reach'] for axis in AXES}
    for i, axis in enumerate(AXES):
        if not box[axis][0] <= anchor[i] <= box[axis][1]:
            raise ValueError(f'The anchor point is outside the reach cap on {axis}')
    while True:
        best = None
        for name, low, high in blocked:
            if not overlaps(box, low, high):
                continue
            cuts = []
            for i, axis in enumerate(AXES):
                for end, edge in ((1, low[i]), (0, high[i])):
                    keeps = anchor[i] <= edge if end else anchor[i] >= edge
                    trial = {key: list(value) for key, value in box.items()}
                    trial[axis][end] = edge
                    if keeps and trial[axis][0] < trial[axis][1]:
                        cuts.append((volume(trial), axis, end, edge, name))
            if not cuts:
                raise ValueError(
                    f'The anchor point sits inside the clearance around {name!r}, so no '
                    'workspace is left. Lower calibration.margin_mm, or anchor the '
                    'workspace somewhere the tool actually fits.')
            top = max(cuts)
            best = top if best is None or top > best else best
        if best is None:
            return box, source
        _, axis, end, edge, name = best
        box[axis][end] = edge
        source[axis][end] = name


def inward(low, high):
    """Round to 0.1 mm, always inwards: rounding never widens a limit."""
    return [math.ceil(low * 10) / 10, math.floor(high * 10) / 10]


def workspace(frames, config, anchor, *, margin_mm=None, reach_mm=None):
    """Bounds for config['workspace_mm'], with the evidence every face came from."""
    tuning = settings(config)
    margin = tuning['margin_mm'] if margin_mm is None else margin_mm
    number(margin, 0, 500, 'margin_mm')
    declared = reach_mm if reach_mm is not None else tuning['reach_mm']
    if declared is not None:
        number(declared, 1, 10000, 'reach_mm')
    reach = declared if declared is not None else kinematic_reach(frames, config)
    tool = tool_envelope(frames, config)
    base = arm_base(frames, config)
    found, skipped = obstacles(frames, config)
    envelope = {axis: [base[i] - reach, base[i] + reach] for i, axis in enumerate(AXES)}
    blocked = [(item['name'], *inflated(item, tool, margin)) for item in found]
    box, source = free_box(envelope, anchor, blocked)
    return {
        'workspace_mm': {axis: inward(*box[axis]) for axis in AXES},
        'bounds_from': {axis: list(source[axis]) for axis in AXES},
        'anchor_mm': [round(value, 3) for value in anchor],
        'arm_base_mm': list(base),
        'margin_mm': margin,
        'reach_mm': reach,
        'reach_declared': declared is not None,
        'tool_mm': {key: round(value, 3) for key, value in tool.items()},
        'obstacles': [{'name': item['name'], 'min': [round(v, 3) for v in item['min']],
                       'max': [round(v, 3) for v in item['max']]} for item in found],
        'skipped': skipped,
    }


async def read_frames(robot, timeout=15):
    """The machine's frame system as plain dicts. Reads configuration only."""
    import asyncio

    from google.protobuf.json_format import MessageToDict

    frames = await asyncio.wait_for(robot.get_frame_system_config(), timeout)
    return [MessageToDict(frame, preserving_proto_field_name=True) for frame in frames]


def describe(result):
    """The derivation as the operator has to review it: bounds, and what set them."""
    tool = result['tool_mm']
    reach = 'calibration.reach_mm' if result['reach_declared'] else 'arm link offsets (upper bound)'
    lines = [
        f'arm base:  ({", ".join(f"{value:.1f}" for value in result["arm_base_mm"])}) mm',
        f'anchor:    ({", ".join(f"{value:.1f}" for value in result["anchor_mm"])}) mm '
        '(the workspace is built around this point)',
        f'tool:      {tool["above_mm"]:.1f} mm above the commanded point, '
        f'{tool["below_mm"]:.1f} mm below, {tool["radius_mm"]:.1f} mm sideways '
        '(from the configured gripper geometry)',
        f'clearance: tool extent + {result["margin_mm"]:.1f} mm margin',
        f'reach cap: {result["reach_mm"]:.1f} mm from the arm base, from {reach}',
        '',
        'obstacles read from the machine (mm, task frame):',
    ]
    for item in result['obstacles']:
        span = '  '.join(f'{axis}[{low:9.1f},{high:9.1f}]' for axis, low, high
                         in zip(AXES, item['min'], item['max']))
        lines.append(f'  {item["name"]:<12} {span}')
    lines.append('frames left out:')
    for item in result['skipped']:
        lines.append(f'  {str(item["frame"]):<12} {item["reason"]}')
    lines += ['', 'workspace_mm (every pose must stay inside):']
    for axis in AXES:
        low, high = result['workspace_mm'][axis]
        least, most = result['bounds_from'][axis]
        lines.append(f'  {axis} [{low:9.1f},{high:9.1f}]   min: {least:<12} max: {most}')
    if any(name == 'reach' for axis in AXES for name in result['bounds_from'][axis]):
        lines.append('  faces marked "reach" are bounded by no configured obstacle, only by the '
                     'reach cap')
    return '\n'.join(lines)
