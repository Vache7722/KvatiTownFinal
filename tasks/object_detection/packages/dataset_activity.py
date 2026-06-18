import json
from typing import List

# Classes the model is trained to detect.
# The index here is the class ID written into YOLO label files.
CLASSES = ['duckie', 'truck', 'sign']

# Images are resized to this square size before training.
IMAGE_SIZE = 416


def convert_labelme_json(json_path: str, img_w: int, img_h: int) -> List[str]:
    """Convert a labelme JSON annotation file to YOLO format label lines.

    Each returned string has the form:
        "<class_id> <cx> <cy> <w> <h>"
    where cx, cy, w, h are normalised to [0, 1] relative to img_w / img_h.
    Shapes whose label is not in CLASSES or whose type is not 'rectangle'
    are silently skipped.
    """
    with open(json_path) as f:
        data = json.load(f)

    lines = []
    for shape in data.get('shapes', []):
        label = shape.get('label', '').lower()
        if label not in CLASSES:
            continue
        if shape.get('shape_type') != 'rectangle':
            continue

        class_id = CLASSES.index(label)

        # labelme stores a rectangle as 2 points [[x1,y1],[x2,y2]] but can
        # also store 4 corners, so take min/max to be safe.
        pts = shape['points']
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)

        cx = ((x1 + x2) / 2) / img_w
        cy = ((y1 + y2) / 2) / img_h
        w  = (x2 - x1) / img_w
        h  = (y2 - y1) / img_h

        # Clamp to valid range in case annotations slightly exceed image bounds
        cx = max(0.0, min(1.0, cx))
        cy = max(0.0, min(1.0, cy))
        w  = max(0.0, min(1.0, w))
        h  = max(0.0, min(1.0, h))

        lines.append(f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

    return lines
