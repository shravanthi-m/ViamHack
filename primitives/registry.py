"""Explicit allowlist shared by the language agent and runtime. No dynamic imports."""
from dataclasses import dataclass
from types import ModuleType

from . import camera, gripper, latte_art, localization, motion, pouring, shake, spoon


@dataclass(frozen=True)
class Tool:
    module: ModuleType
    parameters: dict[str, str]


TOOLS = {
    'localize': Tool(localization, {'object_id': 'object'}),
    'capture': Tool(camera, {'view': 'view'}),
    'close_gripper': Tool(gripper, {'force_percent': 'force'}),
    'open_gripper': Tool(gripper, {}),
    'go_to_origin': Tool(motion, {}),
    'go_to_pose': Tool(motion, {'pose': 'pose'}),
    'pour': Tool(pouring, {'source': 'target', 'target': 'target'}),
    'pick_up': Tool(spoon, {'target': 'target'}),
    'insert_into': Tool(spoon, {'target': 'target'}),
    'stir': Tool(spoon, {'target': 'target', 'duration_s': 'duration'}),
    'shake': Tool(shake, {'duration_s': 'duration'}),
    'place_back': Tool(spoon, {'target': 'target'}),
    'latte_art': Tool(latte_art, {'source': 'target', 'target': 'target', 'letter': 'letter'}),
}


def implementations():
    return {name: getattr(tool.module, name) for name, tool in TOOLS.items()
            if name in tool.module.IMPLEMENTED}


def catalog(config):
    """Machine-readable planner interface, generated from the same registry we dispatch."""
    schemas = {
        'object': {'type': 'string', 'enum': config['objects']},
        'view': {'type': 'string', 'enum': sorted(camera.views(config)),
                 'description': 'A camera the station declares, not a Viam resource name.'},
        'duration': {'type': 'number', 'exclusiveMinimum': 0,
                     'maximum': config['limits']['max_stir_duration_s']},
        'force': {'type': 'number', 'exclusiveMinimum': 0,
                  'maximum': gripper.max_force(config),
                  'description': 'Grip force as a percent of the gripper rated torque, '
                                 'capped by primitive_settings.gripper.max_force_percent.'},
        'target': {'type': 'object', 'properties': {'$ref': {'type': 'string'}},
                   'required': ['$ref'], 'additionalProperties': False,
                   'description': 'ID of an earlier localize step; resolved by the runtime.'},
        'pose': {'type': 'object', 'properties': {
                 **{key: {'type': 'number'} for key in ('x', 'y', 'z')},
                 'yaw': {'type': 'number', 'minimum': -180, 'maximum': 180}},
                 'required': ['x', 'y', 'z', 'yaw'], 'additionalProperties': False,
                 'description': 'Task-frame position in mm and yaw in degrees, tool pointing down.'},
        'letter': {'type': 'string', 'enum': list(latte_art.LETTER_PATHS.keys())},
    }
    ready = implementations()
    return [dict(name=name, description=getattr(tool.module, name).__doc__,
                 implemented=name in ready,
                 parameters={'type': 'object', 'properties': {
                     key: schemas[kind] for key, kind in tool.parameters.items()},
                     'required': list(tool.parameters), 'additionalProperties': False})
            for name, tool in TOOLS.items()]
