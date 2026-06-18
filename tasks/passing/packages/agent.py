import os
import time
import yaml
import cv2
import numpy as np
from collections import deque
from typing import List, Tuple

from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent
from tasks.passing.packages.speed_estimation import SpeedEstimator
from tasks.passing.packages.passing_activity import (
    PassingStateMachine, PULL_OUT, PASSING, MERGE,
)

Detection = Tuple[Tuple[int, int, int, int], float, int]

_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'config', 'passing_config.yaml'
))


class PassingAgent:
    """Lane following + obstacle passing.

    Two lane-following agents run side by side: one on the real camera
    frame and one on the horizontally mirrored frame. In the opposing
    lane the mirrored view looks exactly like normal driving, so the
    mirror agent's commands (with left/right swapped) steer the pass.
    The detector is owned by the server; detections are passed in.
    """

    def __init__(self, config_path: str = None):
        path = config_path or _CONFIG_FILE
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}
        self.cfg = cfg

        self.lane_agent   = LaneServoingAgent()
        self.mirror_agent = LaneServoingAgent()

        estimator = SpeedEstimator(
            vfov_deg          = cfg.get('camera_vfov_deg',    75.0),
            obstacle_height_m = cfg.get('obstacle_height_m',  0.18),
            speed_gain        = cfg.get('speed_gain',         1.0),
        )
        self.sm = PassingStateMachine(cfg, speed_estimator=estimator)

        self.frame_count   = 0
        self.last_commands = (0.0, 0.0)
        self._steer_hist   = deque(maxlen=12)
        self._straight_run = 0
        self._last_state   = None
        self._curve_seen_ts = 0.0
        self.is_curve      = True   # assume curve until proven straight
        self.curve_evidence = False # actual curving observed very recently

    def reset(self):
        """Fresh start: clear the maneuver state and lane-follower filters."""
        self.sm.reset()
        self.lane_agent   = LaneServoingAgent()
        self.mirror_agent = LaneServoingAgent()
        self.last_commands = (0.0, 0.0)
        self._steer_hist.clear()
        self._straight_run = 0
        self.is_curve = True

    def _detect_curve(self, frame_width: int,
                      normal_cmds: Tuple[float, float],
                      mirror_cmds: Tuple[float, float]) -> bool:
        """True when the road ahead is curving.

        The course's detect_curve() is an unfilled stub, so straightness is
        judged here: a lane line whose near and far slice positions differ a
        lot is curving, and so is a stretch where the lane follower has been
        steering hard for a while.

        The signal must come from whichever agent is actually tracking the
        road: while PASSING that is the mirror agent — the normal agent's
        view swings during the maneuver and would read as a fake curve.
        During the open-loop arcs (PULL_OUT/MERGE) nothing tracks the lane,
        so the previous verdict is held.

        The flag is also sticky: it sets immediately on curve evidence but
        needs a sustained run of straight frames to clear, and no-evidence
        frames (obstacle hiding the lines) decide nothing.
        """
        state = self.sm.state
        if state in (PULL_OUT, MERGE):
            self._last_state = state
            return self.is_curve

        # Just exited an arc state: the steering history holds the maneuver
        # transient and would read as a fake curve — start fresh.
        if self._last_state in (PULL_OUT, MERGE):
            self._steer_hist.clear()
            self._straight_run = 0
        self._last_state = state

        if state == PASSING:
            dbg, cmds = self.mirror_agent.last_debug_info, mirror_cmds
        else:
            dbg, cmds = self.lane_agent.last_debug_info, normal_cmds

        line_shift   = 0.0
        has_evidence = False
        for key in ('yellow_xs', 'white_xs'):
            xs = dbg.get(key) or []
            if len(xs) >= 2:
                has_evidence = True
                line_shift = max(line_shift, abs(xs[-1] - xs[0]))

        # Steering only means anything while actually driving — when stopped
        # behind an obstacle the lane follower stares at an occluded scene
        # and its output must not count as curve evidence.
        moving = (abs(self.last_commands[0]) + abs(self.last_commands[1])) / 2.0 > 0.05
        if moving:
            self._steer_hist.append(abs(cmds[0] - cmds[1]))
        steer_mean = (sum(self._steer_hist) / len(self._steer_hist)) if self._steer_hist else 0.0

        # Perspective makes straight lane lines converge too — only a shift
        # well beyond that (the course hint uses 350 px at 1280 wide) is a curve.
        curving = (line_shift > self.cfg.get('curve_line_shift', 0.27) * frame_width
                   or (moving and steer_mean > self.cfg.get('curve_steer_mean', 0.12)))

        if curving:
            self._straight_run = 0
            self._curve_seen_ts = time.monotonic()
        elif has_evidence:
            self._straight_run += 1
        # no evidence: hold the current count (occluded view decides nothing)

        # Two outputs: the sticky flag (conservative, gates pull-outs) and
        # fresh evidence (only actual recent curving, drives the mid-pass
        # bailout — the sticky flag would false-trigger right after resets).
        self.curve_evidence = (time.monotonic() - self._curve_seen_ts) < 0.7
        return self._straight_run < self.cfg.get('curve_clear_frames', 12)

    def compute_commands(self, frame_rgb: np.ndarray,
                         detections: List[Detection],
                         img_size: int) -> Tuple[float, float]:
        self.frame_count += 1

        normal_cmds = self.lane_agent.compute_commands(frame_rgb)
        mirror_cmds = self.mirror_agent.compute_commands(cv2.flip(frame_rgb, 1))
        self.is_curve = self._detect_curve(frame_rgb.shape[1], normal_cmds, mirror_cmds)

        long_straight = self._straight_run >= self.cfg.get('long_straight_frames', 40)
        left, right = self.sm.step(detections, img_size, normal_cmds, mirror_cmds,
                                   is_curve=self.is_curve,
                                   curve_evidence=self.curve_evidence,
                                   long_straight=long_straight)
        self.last_commands = (left, right)
        return left, right

    @property
    def state(self) -> str:
        return self.sm.state

    def status(self) -> dict:
        est = self.sm.estimator
        return {
            'state':            self.sm.state,
            'frames':           self.frame_count,
            'is_curve':         self.is_curve,
            'target_distance':  round(est.latest_distance, 3) if est.latest_distance is not None else None,
            'target_speed':     round(est.latest_speed, 3)    if est.latest_speed    is not None else None,
            'speed_confident':  est.confident,
            'locked_speed':     round(self.sm.target_speed, 3)    if self.sm.target_speed    is not None else None,
            'last_measurement': round(self.sm.last_measurement, 3) if self.sm.last_measurement is not None else None,
            'commands':         [round(c, 3) for c in self.last_commands],
        }
