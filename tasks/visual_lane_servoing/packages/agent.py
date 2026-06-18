import os
import yaml
import numpy as np
import cv2
from collections import deque
from typing import Tuple

from tasks.visual_lane_servoing.packages import visual_servoing_activity as student
from tasks.visual_lane_servoing.packages.cuvrve_behavior import detect_curve

_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'config', 'lane_servoing_config.yaml'
))

_LINE_OFFSET = 160
_ROI_START   = 0.47
_NUM_SLICES  = 3
_SLICE_TOL   = 5


def detect_lines_in_slices(
    mask_yellow: np.ndarray,
    mask_white:  np.ndarray,
    h: int,
) -> Tuple[list, list]:
    slice_height = int(h * 0.35 / _NUM_SLICES)
    start_y      = int(h * _ROI_START)
    yellow_xs, white_xs = [], []

    for i in range(_NUM_SLICES):
        y = start_y + i * slice_height + slice_height // 2

        strip_y = mask_yellow[y - _SLICE_TOL: y + _SLICE_TOL, :]
        idx = np.where(strip_y > 0)[1]
        if len(idx) > 0:
            yellow_xs.append(int(np.mean(idx)))

        strip_w = mask_white[y - _SLICE_TOL: y + _SLICE_TOL, :]
        idx = np.where(strip_w > 0)[1]
        if len(idx) > 0:
            white_xs.append(int(np.mean(idx)))

    return yellow_xs, white_xs


class LaneServoingAgent:

    def __init__(self, config_path: str = None):
        path = config_path or _CONFIG_FILE
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}

        self.p_gain              = cfg.get('p_gain',              0.1)
        self.d_gain              = cfg.get('d_gain',              0.35)
        self.max_steer           = cfg.get('max_steer',           0.4)
        self.base_speed          = cfg.get('base_speed',          0.2)
        self.curve_speed         = cfg.get('curve_speed',         0.2)
        self.curve_threshold     = cfg.get('curve_threshold',     350)
        self.curve_boost         = cfg.get('curve_boost',         1.3)
        self.curve_hold_frames   = cfg.get('curve_hold_frames',   6)
        self.detection_threshold = cfg.get('detection_threshold', 500)

        self.frame_count        = 0
        self._speed             = self.base_speed
        self._prev_error        = 0.0
        self._filtered_error    = 0.0
        self._last_raw_error    = 0.0
        self._lane_half_width   = float(_LINE_OFFSET)
        self._left_history      = deque(maxlen=3)
        self._right_history     = deque(maxlen=3)
        self._curve_hold        = 0
        self.last_debug_info    = self._empty_debug_info(480, 640)

    def _calculate_error(self, yellow_xs, white_xs, left_det, right_det, w):
        if left_det and right_det and yellow_xs and white_xs:
            y_mean = float(np.mean(yellow_xs))
            w_mean = float(np.mean(white_xs))
            measured = (w_mean - y_mean) / 2.0
            if measured > 20:
                self._lane_half_width = 0.9 * self._lane_half_width + 0.1 * measured
            error = w / 2.0 - (y_mean + w_mean) / 2.0
        elif left_det and yellow_xs:
            error = w / 2.0 - (float(np.mean(yellow_xs)) + self._lane_half_width)
        elif right_det and white_xs:
            error = w / 2.0 - (float(np.mean(white_xs)) - self._lane_half_width)
        else:
            # Lines briefly lost (typically mid-corner): keep steering with the
            # last real measurement instead of letting the error decay to zero,
            # which would straighten the robot out of the turn.
            return self._last_raw_error

        error = float(np.clip(error / (w / 2.0), -1.0, 1.0))
        self._last_raw_error = error
        return error

    def _calculate_steering(self, error: float, is_curve: bool) -> float:
        error_diff       = error - self._prev_error
        self._prev_error = error
        p = self.p_gain * error
        d = self.d_gain * error_diff
        if is_curve:
            # More P authority in corners, but damp D: the lighter error
            # filter used on curves makes the frame-to-frame diff noisy,
            # and amplifying that shakes the robot through the turn.
            p *= self.curve_boost
            d *= 0.5
        return float(np.clip(p + d, -self.max_steer, self.max_steer))

    def _motor_commands(self, steering: float, recovery: bool, is_curve: bool, both_visible: bool):
        if recovery:
            return 0.0, 0.0

        target = self.curve_speed if is_curve else self.base_speed
        if not both_visible:
            target *= 0.8
        # Asymmetric ramp: brake for a corner immediately (entering fast is
        # how the robot runs wide), but accelerate smoothly — the curve flag
        # flickers at corner boundaries and hard speed-up steps mid-turn are
        # what shakes the robot.
        if target < self._speed:
            self._speed = target
        else:
            self._speed = 0.75 * self._speed + 0.25 * target

        left  = self._speed - steering
        right = self._speed + steering
        return float(np.clip(left, 0.0, 1.0)), float(np.clip(right, 0.0, 1.0))

    def _smooth(self, left, right, is_curve, both_visible):
        # Smoothing trades twitchiness for lag; in a chicane the lag is what
        # drives the robot off the road, so curves get raw commands.
        buf = 1 if is_curve else (2 if both_visible else 1)
        if self._left_history.maxlen != buf:
            self._left_history  = deque(maxlen=buf)
            self._right_history = deque(maxlen=buf)
        self._left_history.append(left)
        self._right_history.append(right)
        return (sum(self._left_history)  / len(self._left_history),
                sum(self._right_history) / len(self._right_history))

    def compute_commands(self, image: np.ndarray) -> Tuple[float, float]:
        self.frame_count += 1
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        try:
            mask_left, mask_right = student.detect_lane_markings(bgr)
        except Exception as e:
            print(f"[Agent] detect_lane_markings error: {e}")
            return 0.0, 0.0

        mask_y = (mask_left  * 255).astype(np.uint8)
        mask_w = (mask_right * 255).astype(np.uint8)

        yellow_pixels = int(np.count_nonzero(mask_y))
        white_pixels  = int(np.count_nonzero(mask_w))
        total_pixels  = yellow_pixels + white_pixels

        combined = np.clip(mask_left + mask_right, 0, 1)
        self.last_debug_info = {
            'roi':               image,
            'lane_mask':         (combined * 255).astype(np.uint8),
            'white_mask':        mask_w,
            'yellow_mask':       mask_y,
            'total_lane_pixels': total_pixels,
            'lateral_error':     float(np.clip(self._prev_error, -1.0, 1.0)),
            'lane_detected':     total_pixels >= self.detection_threshold,
            'frame_count':       self.frame_count,
        }

        h, w      = mask_y.shape
        left_det  = yellow_pixels > 0
        right_det = white_pixels  > 0
        recovery  = total_pixels  < self.detection_threshold

        yellow_xs, white_xs = detect_lines_in_slices(mask_y, mask_w, h)
        both_visible        = left_det and right_det and not recovery
        curve_now, curve_dir = detect_curve(yellow_xs, white_xs, self.curve_threshold)

        # Hold the curve verdict for a few frames: in the middle of a corner
        # the lines can momentarily read straight (or vanish), and dropping
        # back to full speed there is exactly the chicane failure mode.
        if curve_now:
            self._curve_hold = self.curve_hold_frames
        else:
            self._curve_hold = max(0, self._curve_hold - 1)
        is_curve = self._curve_hold > 0

        raw_error = self._calculate_error(yellow_xs, white_xs, left_det, right_det, w)
        # Less filtering in corners: responsiveness matters more than jitter.
        alpha = 0.45 if is_curve else 0.7
        self._filtered_error = alpha * self._filtered_error + (1.0 - alpha) * raw_error
        steering             = self._calculate_steering(self._filtered_error, is_curve)
        left, right          = self._motor_commands(steering, recovery, is_curve, both_visible)
        left, right          = self._smooth(left, right, is_curve, both_visible)

        slice_height = int(h * 0.35 / _NUM_SLICES)
        start_y      = int(h * _ROI_START)
        self.last_debug_info.update({
            'yellow_xs': yellow_xs,
            'white_xs':  white_xs,
            'slice_ys':  [start_y + i * slice_height + slice_height // 2 for i in range(_NUM_SLICES)],
            'is_curve':  is_curve,
            'curve_dir': curve_dir,
        })

        return left, right

    def step(self, image: np.ndarray, wheels_driver) -> Tuple[float, float]:
        left, right = self.compute_commands(image)
        wheels_driver.set_wheels_speed(left, right)
        return left, right

    def get_debug_info(self, image: np.ndarray) -> dict:
        return self.last_debug_info

    def _empty_debug_info(self, h, w):
        return {
            'roi':               np.zeros((h, w, 3), dtype=np.uint8),
            'lane_mask':         np.zeros((h, w),    dtype=np.uint8),
            'white_mask':        np.zeros((h, w),    dtype=np.uint8),
            'yellow_mask':       np.zeros((h, w),    dtype=np.uint8),
            'total_lane_pixels': 0,
            'lateral_error':     0.0,
            'lane_detected':     False,
            'frame_count':       0,
        }
