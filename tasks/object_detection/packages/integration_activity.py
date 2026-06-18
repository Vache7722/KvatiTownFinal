from typing import Tuple

# Path to the trained model weights (.onnx file).
# Relative paths resolve from the project root.
MODEL_PATH = "tasks/object_detection/models/best.onnx"


def NUMBER_FRAMES_SKIPPED() -> int:
    # Run inference every 3rd frame — enough to catch slow-moving obstacles
    # while keeping CPU load manageable.
    return 2


def filter_by_classes(pred_class: int) -> bool:
    """Keep duckies (0) and duckiebots/trucks (1). Ignore signs (2)."""
    return pred_class in (0, 1)


def filter_by_scores(score: float) -> bool:
    """Drop predictions below 40% confidence to reduce false positives."""
    return score >= 0.40


def filter_by_bboxes(bbox: Tuple[int, int, int, int]) -> bool:
    """Drop noise and very distant objects based on bounding box size."""
    xmin, ymin, xmax, ymax = bbox
    width  = xmax - xmin
    height = ymax - ymin
    # Ignore boxes smaller than ~3% of a 416px frame in either dimension
    if width < 12 or height < 12:
        return False
    return True
