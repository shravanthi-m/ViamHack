"""Presentation vocabulary; extra display labels never expand robot task objects."""
from primitives.vision import DEFAULT_MODEL, MAX_IMAGE_BYTES, image_type, observe, validate_observation
from primitives.vision import select_objects as select_task_objects


def select_objects(config, requested=None):
    # The capped metal shaker is a display-only identity, not a new manipulation target.
    display_config = {**config, 'objects': list(dict.fromkeys([*config['objects'], 'shaker']))}
    if requested is None:
        requested = ','.join([*select_task_objects(config), 'shaker'])
    return select_task_objects(display_config, requested)
