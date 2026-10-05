"""Object recognition integration for DODO.

Uses Python-based YOLOv8 (ultralytics) for object detection.
Falls back gracefully if ultralytics is not installed.

Can detect objects from:
  - A screenshot (PIL Image or base64 string)
  - A file path (image or video frame)
  - The live camera feed
"""

from __future__ import annotations

import base64
import io
import os
import logging
from typing import List, Dict, Optional, Union

from core.memory import load_config

logger = logging.getLogger(__name__)

# ── Lazy-loaded model cache ──────────────────────────────────────────────────

_model = None
_cached_model_name = None


def _get_model():
    """Load the YOLO model (configurable: yolov8s.pt, yolov8m.pt, etc.)."""
    global _model, _cached_model_name
    cfg = load_config()
    target_model = cfg.get("yolo_model", "yolov8s.pt")

    if _model is None or _cached_model_name != target_model:
        try:
            from ultralytics import YOLO
            _model = YOLO(target_model)
            _cached_model_name = target_model
            logger.info("YOLO model loaded: %s", target_model)
        except ImportError:
            raise RuntimeError(
                "Object detection requires 'ultralytics'. "
                "Install with: pip install ultralytics"
            )
    return _model


# ── Public API ───────────────────────────────────────────────────────────────

def recognize_objects(
    source: Optional[Union[str, "PIL.Image.Image"]] = None,
    conf: float = 0.40,
    max_detections: int = 20,
) -> List[Dict]:
    """Detect objects in an image or from camera.

    Args:
        source: One of:
            - None → capture from default camera (single frame)
            - str path → image file on disk
            - str base64 → base64-encoded image data
            - PIL.Image.Image → in-memory image
        conf: Confidence threshold (0-1). Default 0.25.
        max_detections: Max number of detections to return.

    Returns:
        List of dicts with keys: 'class', 'confidence', 'bbox' (x1,y1,x2,y2).
    """
    model = _get_model()

    # Handle base64 input
    if isinstance(source, str) and not os.path.exists(source):
        try:
            img_bytes = base64.b64decode(source)
            from PIL import Image
            source = Image.open(io.BytesIO(img_bytes))
        except Exception:
            pass  # Not base64, let YOLO try it as-is

    # Handle None → camera capture
    if source is None:
        source = _capture_camera_frame()
        if source is None:
            return [{"class": "error", "confidence": 0.0,
                     "bbox": [], "note": "Could not open camera"}]

    # Run inference with NMS IoU threshold to eliminate duplicate boxes
    results = model(source, conf=conf, iou=0.45, verbose=False)

    detections: List[Dict] = []
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        for i, box in enumerate(boxes):
            if i >= max_detections:
                break
            cls_id = int(box.cls[0])
            cls_name = model.names.get(cls_id, f"class_{cls_id}")
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append({
                "class": cls_name,
                "confidence": round(confidence, 3),
                "bbox": [round(x1), round(y1), round(x2), round(y2)],
            })

    # Sort by confidence descending
    detections.sort(key=lambda d: d["confidence"], reverse=True)
    return detections[:max_detections]


def recognize_objects_summary(
    source: Optional[Union[str, "PIL.Image.Image"]] = None,
    conf: float = 0.40,
) -> str:
    """Detect objects and return a human-readable summary string.

    Useful for feeding directly into the LLM context.
    """
    detections = recognize_objects(source, conf=conf)
    if not detections:
        return "No objects detected."

    # Group by class
    from collections import Counter
    counts = Counter(d["class"] for d in detections)
    parts = []
    for cls, count in counts.most_common():
        avg_conf = sum(
            d["confidence"] for d in detections if d["class"] == cls
        ) / count
        if count == 1:
            parts.append(f"{cls} ({avg_conf:.0%})")
        else:
            parts.append(f"{count}x {cls} ({avg_conf:.0%})")

    return "Detected: " + ", ".join(parts)


def detect_from_screenshot(img_b64: str, conf: float = 0.40) -> str:
    """Convenience: detect objects from a base64-encoded screenshot.

    Returns a summary string suitable for LLM context injection.
    """
    return recognize_objects_summary(img_b64, conf=conf)


# ── Camera helper ────────────────────────────────────────────────────────────

def _capture_camera_frame():
    """Grab a high-resolution frame from the camera with sensor warmup."""
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            logger.warning("Cannot open default camera")
            return None

        # Request 720p HD resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # Discard warmup frames so hardware auto-exposure & focus settle
        ret = False
        frame = None
        for _ in range(6):
            ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            logger.warning("Cannot read frame from camera")
            return None

        # Convert BGR → RGB for consistency
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        from PIL import Image
        return Image.fromarray(frame_rgb)
    except ImportError:
        logger.warning("OpenCV not available for camera capture")
        return None
    except Exception as e:
        logger.warning("Camera capture failed: %s", e)
        return None

