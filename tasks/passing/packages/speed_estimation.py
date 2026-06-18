import math
import time
from collections import deque
from typing import Optional, Tuple

Detection = Tuple[Tuple[int, int, int, int], float, int]


def distance_from_bbox(bbox: Tuple[int, int, int, int],
                       img_size: int,
                       vfov_deg: float,
                       obstacle_height_m: float) -> float:
    """Pinhole distance estimate from the bounding-box height.

    Height is used rather than width because it stays constant when the
    target vehicle turns (its side view is much wider than its rear view,
    which would corrupt the distance and hence the speed fit).

    Detections live in the square img_size x img_size space the model sees;
    the full vertical FOV maps onto img_size pixels.
    """
    _, ymin, _, ymax = bbox
    h_px = max(1.0, float(ymax - ymin))
    f_py = (img_size / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    return obstacle_height_m * f_py / h_px


class SpeedEstimator:
    """Estimates the absolute speed of the vehicle ahead.

    Per frame it records (t, distance-to-target, own speed). The target's
    speed is recovered as:

        v_target = v_ego + dZ/dt

    where dZ/dt is the slope of a least-squares line fit over a short
    window of distance samples, and v_ego is the mean own speed over the
    same window (own speed = mean wheel command * speed_gain).
    """

    def __init__(self,
                 vfov_deg: float = 75.0,
                 obstacle_height_m: float = 0.18,
                 speed_gain: float = 1.0,
                 window_seconds: float = 2.0,
                 min_samples: int = 8,
                 min_span_seconds: float = 0.9):
        self.vfov_deg          = vfov_deg
        self.obstacle_height_m = obstacle_height_m
        self.speed_gain        = speed_gain
        self.window_seconds    = window_seconds
        self.min_samples       = min_samples
        self.min_span_seconds  = min_span_seconds

        self._samples: deque = deque()   # (t, distance_m, v_ego_mps)
        self.latest_distance: Optional[float] = None
        self.latest_speed:    Optional[float] = None

    def reset(self):
        self._samples.clear()
        self.latest_distance = None
        self.latest_speed    = None

    def update(self, bbox: Tuple[int, int, int, int], img_size: int,
               ego_cmd_avg: float, now: float = None) -> float:
        """Add one observation. Returns the distance estimate in meters."""
        if now is None:
            now = time.monotonic()

        dist  = distance_from_bbox(bbox, img_size, self.vfov_deg, self.obstacle_height_m)
        v_ego = ego_cmd_avg * self.speed_gain

        self._samples.append((now, dist, v_ego))
        while self._samples and now - self._samples[0][0] > self.window_seconds:
            self._samples.popleft()

        self.latest_distance = dist
        self._refit()
        return dist

    def _refit(self):
        n = len(self._samples)
        if n < self.min_samples:
            return
        t0 = self._samples[0][0]
        if self._samples[-1][0] - t0 < self.min_span_seconds:
            return

        # Least-squares slope of distance over time.
        ts  = [s[0] - t0 for s in self._samples]
        zs  = [s[1] for s in self._samples]
        t_m = sum(ts) / n
        z_m = sum(zs) / n
        den = sum((t - t_m) ** 2 for t in ts)
        if den < 1e-9:
            return
        dz_dt = sum((t - t_m) * (z - z_m) for t, z in zip(ts, zs)) / den

        v_ego_mean = sum(s[2] for s in self._samples) / n
        self.latest_speed = max(0.0, v_ego_mean + dz_dt)

    @property
    def confident(self) -> bool:
        return self.latest_speed is not None

    def is_stationary(self, threshold: float = 0.03) -> bool:
        return self.confident and self.latest_speed < threshold
