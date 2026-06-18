from typing import Dict, Tuple
import logging
logger = logging.getLogger(__name__)

SPEED = 1
TURN = 0.5


def get_motor_speeds(keys_pressed: Dict[str, bool]) -> Tuple[float, float]:
    left_speed = 0.0
    right_speed = 0.0


    if keys_pressed['up']:
       left_speed += SPEED
       right_speed += SPEED

    if keys_pressed['down']:
     left_speed -= SPEED
     right_speed -= SPEED

    if keys_pressed['left']:
     left_speed -=  TURN
     right_speed += TURN

    if keys_pressed['right']:
     left_speed += TURN
     right_speed -= TURN


    return left_speed, right_speed
