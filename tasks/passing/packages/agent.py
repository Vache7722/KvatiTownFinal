"""Passing task agent — lane following reused from visual lane servoing."""

from typing import Tuple

import numpy as np

from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent


class PassingAgent(LaneServoingAgent):
    """Autonomous lane follower for the passing map.

    Uses the same visual servoing pipeline as ``visual_lane_servoing``:
    HSV lane masks → slice sampling → lateral error → PD steering → wheel PWM.
    Passing-specific logic (overtake, merge) can be added here later.
    """

    def compute_commands(self, image: np.ndarray) -> Tuple[float, float]:
        return super().compute_commands(image)
