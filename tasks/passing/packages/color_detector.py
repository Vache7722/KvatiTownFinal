"""Fallback obstacle detector by chassis color.

When no trained YOLO model (best.onnx) is available, duckiebots can
still be found by color. Detections are returned in the same
(bbox, score, class_id) format and the same square img_size coordinate
space as the ONNX detector, so the passing logic cannot tell the
difference.

The HSV bounds are constructor parameters: the simulated chassis is a
saturated dark navy, the real DB21 chassis is a brighter blue. A trained
best.onnx in tasks/object_detection/models/ is still the better detector
for the real world — color matching will trigger on anything
chassis-blue in the camera's view of the lane.
"""

import cv2
import numpy as np
from typing import List, Tuple

Detection = Tuple[Tuple[int, int, int, int], float, int]

# Saturated dark blue in HSV (OpenCV hue range 0-179) — the sim chassis.
SIM_LOWER = (100,  80,  30)
SIM_UPPER = (135, 255, 180)

# The real DB21 chassis: vivid, strongly saturated blue. The S floor is
# the load-bearing part — the gray-blue track mat and its dark stripes
# also sit in this hue band under indoor light, but at low saturation.
REAL_LOWER = (100, 130,  70)
REAL_UPPER = (130, 255, 255)

_MIN_BOX_PX  = 10     # in img_size space, reject specks
_TRUCK_CLASS = 1


class ColorFallbackDetector:

    def __init__(self, img_size: int = 416, lower=SIM_LOWER, upper=SIM_UPPER):
        self.img_size = img_size
        self.lower    = np.array(lower)
        self.upper    = np.array(upper)

    def detect(self, frame_rgb: np.ndarray) -> List[Detection]:
        small = cv2.resize(frame_rgb, (self.img_size, self.img_size))
        hsv   = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
        mask  = cv2.inRange(hsv, self.lower, self.upper)
        # Open kills isolated speckles (textured floor pixels), close heals
        # the chassis blob around bolts/plates.
        mask  = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  np.ones((3, 3), np.uint8))
        mask  = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: List[Detection] = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < _MIN_BOX_PX or h < _MIN_BOX_PX:
                continue
            # A chassis is a solid blob; a cluster of floor speckles bounds
            # a mostly-empty box. Require the box to be filled with mask.
            fill = float(np.count_nonzero(mask[y:y + h, x:x + w])) / (w * h)
            if fill < 0.45:
                continue
            # The blue box is the chassis; wheels below it add ~25% height,
            # so extend the bbox bottom to approximate the full vehicle.
            y2 = min(self.img_size - 1, int(y + h * 1.3))
            detections.append(((x, y, x + w, y2), 0.99, _TRUCK_CLASS))

        # Closest (largest y2) first; cap the list — the passing logic only
        # needs the few nearest vehicles, and a noise frame must not flood
        # the lane checks with phantom boxes.
        detections.sort(key=lambda d: d[0][3], reverse=True)
        return detections[:4]
