"""
DODO — skills/webcam_vision.py
Webcam capture, real-time preview, and AI vision analysis.

Capabilities:
  1. Instant Snapshot: Grab a frame and run YOLOv8 object detection
  2. Vision Analysis: Send frame to Vision AI for contextual answering
  3. Live Preview: Open a live camera window with real-time YOLO bounding boxes
"""

from __future__ import annotations

import base64
import io
import logging
import threading
import time
from typing import Optional, Tuple

import cv2
from PIL import Image

from core.memory import load_config
from core.object_recognition import recognize_objects, recognize_objects_summary

logger = logging.getLogger(__name__)

# ── Live Preview State ────────────────────────────────────────────────────────
_preview_thread: Optional[threading.Thread] = None
_preview_stop = threading.Event()


def is_camera_available(device_index: int = 0) -> bool:
    """Check if webcam is accessible."""
    try:
        cap = cv2.VideoCapture(device_index)
        if not cap.isOpened():
            return False
        ret, _ = cap.read()
        cap.release()
        return bool(ret)
    except Exception:
        return False


def capture_webcam_frame(device_index: int = 0) -> Optional[Image.Image]:
    """Capture a high-definition frame from the webcam with sensor warmup."""
    try:
        cap = cv2.VideoCapture(device_index)
        if not cap.isOpened():
            logger.warning("Could not open webcam device %s", device_index)
            return None

        # Request 720p HD resolution for sharp object boundaries
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # Discard warmup frames so hardware auto-exposure, auto-focus & white-balance settle
        ret = False
        frame = None
        for _ in range(7):
            ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return None

        # Convert BGR (OpenCV) to RGB (PIL)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb_frame)
    except Exception as e:
        logger.error("Failed to capture webcam frame: %s", e)
        return None


def capture_webcam_b64(device_index: int = 0, quality: int = 80) -> Tuple[Optional[str], Optional[Image.Image]]:
    """Capture webcam frame and return (base64_jpeg_string, PIL_image)."""
    img = capture_webcam_frame(device_index)
    if img is None:
        return None, None

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    return b64_str, img


def analyze_webcam(question: str = "What do you see through the camera?") -> str:
    """
    Look through the webcam and answer what is seen.
    Uses a 2-tier pipeline:
      1. Local YOLOv8 nano model detects objects instantly (0 ms API latency, 100% offline).
      2. If vision API is available, sends image + YOLO hints to Vision LLM.
      3. If vision API is rate-limited or fails, returns YOLO detection summary.
    """
    b64_str, pil_img = capture_webcam_b64()
    if pil_img is None or b64_str is None:
        return "⚠️ Could not access your webcam. Make sure no other application is using it."

    # Tier 1: Local YOLOv8 detection
    try:
        yolo_summary = recognize_objects_summary(pil_img)
    except Exception as e:
        yolo_summary = f"(Local detection error: {e})"

    # Tier 2: Try Vision LLMs (NVIDIA Llama-3.2-Vision > Gemini Vision)
    cfg = load_config()
    from openai import OpenAI

    # Candidate vision endpoints in priority order
    vision_candidates = []

    # Priority 1: NVIDIA Meta Llama-3.2-11b Vision (high throughput, no 20-req quota, fast)
    nvidia_key = cfg.get("nvidia_api_key", "")
    if nvidia_key:
        vision_candidates.append({
            "name": "NVIDIA Vision",
            "client": OpenAI(api_key=nvidia_key, base_url="https://integrate.api.nvidia.com/v1"),
            "model": cfg.get("nvidia_model", "meta/llama-3.2-11b-vision-instruct"),
        })

    # Priority 2: Google Gemini 3.8 Flash Vision
    gemini_key = cfg.get("gemini_api_key", "")
    if gemini_key:
        vision_candidates.append({
            "name": "Gemini Vision",
            "client": OpenAI(api_key=gemini_key, base_url="https://generativelanguage.googleapis.com/v1beta/openai/"),
            "model": cfg.get("gemini_model", "gemini-3.8-flash"),
        })

    prompt_text = (
        "You are DODO looking through the user's webcam. "
        "Provide an observant, natural, and direct response to the user's question.\n"
        "- If asked about the person: describe their gender, approximate age, facial features, hair, expressions, and clothing.\n"
        "- If asked about objects or what they are holding: carefully identify any handheld items, accessories, tools, and background objects.\n"
        f"- Local object detector noted: {yolo_summary}.\n\n"
        f"User's Question: {question}"
    )

    for candidate in vision_candidates:
        try:
            client = candidate["client"]
            model = candidate["model"]
            response = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}}
                    ]
                }],
                max_tokens=1024,
                temperature=0.3,
            )
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
        except Exception as e:
            logger.warning("%s call failed on webcam frame (%s), trying next provider", candidate["name"], e)

    # Fallback to local YOLO detection if all cloud vision providers fail
    return f"📷 Camera feed snapshot (local detection):\n{yolo_summary}"


def start_camera_preview(device_index: int = 0) -> str:
    """
    Launch a real-time live preview window with YOLOv8 bounding boxes.
    Runs in a background thread so the rest of DODO remains responsive.
    Press 'q' in the camera window to close.
    """
    global _preview_thread, _preview_stop

    if _preview_thread and _preview_thread.is_alive():
        return "Camera preview is already active."

    _preview_stop.clear()

    def _preview_loop():
        try:
            from core.object_recognition import _get_model
            model = _get_model()
        except Exception:
            model = None

        cap = cv2.VideoCapture(device_index)
        if not cap.isOpened():
            logger.error("Failed to open camera for preview.")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        window_name = "DODO — Live Camera Preview (Press 'q' to close)"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        prev_time = time.time()
        fps = 0.0

        while not _preview_stop.is_set():
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            curr_time = time.time()
            fps = 0.9 * fps + 0.1 * (1.0 / max(curr_time - prev_time, 1e-4))
            prev_time = curr_time

            # Run YOLO object detection
            if model:
                try:
                    results = model(frame, conf=0.40, iou=0.45, verbose=False)
                    frame = results[0].plot()
                except Exception:
                    pass

            # Run Face Landmark & Expression Recognition
            try:
                import skills.face_expression as fe
                face_data = fe.analyze_face(frame)
                frame = fe.draw_face_annotations(frame, face_data)
            except Exception:
                pass

            # Overlay title and FPS
            cv2.putText(
                frame,
                f"DODO Vision & Expression Engine | FPS: {fps:.1f} | Press 'q' to close",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (0, 255, 170),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

        cap.release()
        cv2.destroyAllWindows()

    _preview_thread = threading.Thread(target=_preview_loop, daemon=True)
    _preview_thread.start()
    return "📷 Live camera preview started. A window has opened with real-time detection."


def stop_camera_preview() -> str:
    """Close the live camera preview window."""
    global _preview_thread, _preview_stop
    if _preview_thread and _preview_thread.is_alive():
        _preview_stop.set()
        _preview_thread.join(timeout=2.0)
        cv2.destroyAllWindows()
        return "Camera preview closed."
    return "Camera preview is not running."
