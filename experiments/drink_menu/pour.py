from viam.components.arm import Arm

async def pour(arm: Arm, height: float, tilt_angle: float):
    # tilt-and-pour motion using arm.move_to_position or move_to_joint_positions
    pass

async def pour_cycle(arm: Arm, drink_config: dict):
    for _ in range(drink_config["num_pours"]):
        await pour(arm, drink_config["pour_height"], drink_config["tilt_angle"])