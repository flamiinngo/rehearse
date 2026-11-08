"""Facing-the-camera estimate from a recorded video (OpenCV YuNet, sampled frames).

YuNet returns five landmarks per face. Head yaw is read from where the nose sits
between the eyes, pitch from where it sits between the eyes and the mouth: a
frontal, level face counts as eye contact. It is a head-direction proxy, not gaze
tracking, and the report says so.

The model is fetched once from OpenCV's own repository at a pinned commit and
checked against its SHA-256 before use (catalog rule 17).
"""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from pathlib import Path

from .delivery import FaceSample

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/f12e12798e8314f7c074a6656816c048dcc95b7a"
    "/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

SAMPLE_FPS = 2.0
FRAME_WIDTH = 320
YAW_LIMIT = 0.32
PITCH_RANGE = (0.30, 0.78)

_lock = threading.Lock()


class FaceUnavailable(RuntimeError):
    """OpenCV or the face model could not be loaded."""


def ensure_model(data_dir: Path) -> Path:
    path = data_dir / "models" / MODEL_NAME
    with _lock:
        if path.exists() and _sha256(path) == MODEL_SHA256:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, open(tmp, "wb") as out:
                out.write(resp.read())
        except OSError as exc:
            raise FaceUnavailable(f"Could not download the face model: {exc}") from exc
        if _sha256(tmp) != MODEL_SHA256:
            tmp.unlink(missing_ok=True)
            raise FaceUnavailable("The downloaded face model failed its checksum and was discarded.")
        tmp.replace(path)
        return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(face_row) -> str:
    """``facing`` or ``away`` for one YuNet detection row (x, y, w, h, 5 landmarks, score)."""
    rex, rey, lex, ley, nx, ny, rmx, rmy, lmx, lmy = (float(v) for v in face_row[4:14])
    eye_mid_x, eye_mid_y = (rex + lex) / 2, (rey + ley) / 2
    eye_dist = max(abs(lex - rex), 1.0)
    yaw = (nx - eye_mid_x) / eye_dist
    mouth_mid_y = (rmy + lmy) / 2
    pitch = (ny - eye_mid_y) / max(mouth_mid_y - eye_mid_y, 1.0)
    facing = abs(yaw) <= YAW_LIMIT and PITCH_RANGE[0] <= pitch <= PITCH_RANGE[1]
    return "facing" if facing else "away"


def sample_video(path: str, model_path: Path) -> list[FaceSample]:
    """Facing state at ``SAMPLE_FPS`` across the video; ``[]`` when the file has no video track."""
    try:
        import av
        import cv2
    except ImportError as exc:
        raise FaceUnavailable("OpenCV is not installed.") from exc

    samples: list[FaceSample] = []
    detector = None
    with av.open(path) as container:
        if not container.streams.video:
            return []
        next_t = 0.0
        for frame in container.decode(video=0):
            t = float(frame.time or 0.0)
            if t < next_t:
                continue
            next_t = t + 1.0 / SAMPLE_FPS
            img = frame.to_ndarray(format="bgr24")
            h, w = img.shape[:2]
            if w > FRAME_WIDTH:
                img = cv2.resize(img, (FRAME_WIDTH, int(h * FRAME_WIDTH / w)))
                h, w = img.shape[:2]
            if detector is None:
                detector = cv2.FaceDetectorYN.create(str(model_path), "", (w, h), 0.7, 0.3, 5)
            detector.setInputSize((w, h))
            _, faces = detector.detect(img)
            if faces is None or len(faces) == 0:
                samples.append(FaceSample(t, "none"))
                continue
            largest = max(faces, key=lambda f: f[2] * f[3])
            samples.append(FaceSample(t, classify(largest)))
    return samples
