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


def model_ready(data_dir: Path) -> bool:
    return (data_dir / "models" / MODEL_NAME).exists()


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


_live_detector: dict[str, object] = {}
_live_lock = threading.Lock()

# Cue priority: the first problem found is the one shown, so the nudge never flickers between several.
CUES = {
    "away": "Look at the camera",
    "cut_off": "You're cut off: re-centre yourself",
    "too_close": "Move back a little",
    "too_far": "Move a little closer",
    "off_centre": "Centre yourself in the frame",
    "dark": "Add light in front of you",
}


def check_frame(jpeg: bytes, model_path: Path) -> dict:
    """Live presence on one small camera frame: facing, framing and light, plus the one cue to show."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise FaceUnavailable("OpenCV is not installed.") from exc
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("The camera frame could not be read.")
    h, w = img.shape[:2]
    if w > FRAME_WIDTH:
        img = cv2.resize(img, (FRAME_WIDTH, int(h * FRAME_WIDTH / w)))
        h, w = img.shape[:2]
    with _live_lock:
        detector = _live_detector.get(str(model_path))
        if detector is None:
            detector = _live_detector[str(model_path)] = cv2.FaceDetectorYN.create(str(model_path), "", (w, h), 0.7, 0.3, 5)
        detector.setInputSize((w, h))
        _, faces = detector.detect(img)
    if faces is None or len(faces) == 0:
        return {"face": False, "facing": False, "framing": "none", "light": "unknown",
                "cue": "We can't see your face", "center": None}
    f = max(faces, key=lambda r: r[2] * r[3])
    x, y, fw, fh = (float(v) for v in f[:4])
    cx, size = (x + fw / 2) / w, fw / w
    if x < 2 or y < 2 or x + fw > w - 2 or y + fh > h - 2:
        framing = "cut_off"
    elif size > 0.5:
        framing = "too_close"
    elif size < 0.13:
        framing = "too_far"
    elif abs(cx - 0.5) > 0.22:
        framing = "off_centre"
    else:
        framing = "ok"
    region = img[max(0, int(y)):int(y + fh), max(0, int(x)):int(x + fw)]
    light = "dark" if region.size and float(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).mean()) < 60 else "ok"
    facing = classify(f) == "facing"
    problem = next((k for k, bad in (("away", not facing), ("cut_off", framing == "cut_off"),
                                      ("too_close", framing == "too_close"), ("too_far", framing == "too_far"),
                                      ("off_centre", framing == "off_centre"), ("dark", light == "dark")) if bad), None)
    return {"face": True, "facing": facing, "framing": framing, "light": light,
            "cue": CUES[problem] if problem else None, "center": [round(cx, 3), round((y + fh / 2) / h, 3)]}


def summarize_presence(samples: list[dict]) -> dict | None:
    """Live samples from one answer: share of time facing, the cues shown, and how much the head moved."""
    usable = [s for s in samples if isinstance(s, dict)][:900]
    if not usable:
        return None
    seen = [s for s in usable if s.get("face")]
    cues: dict[str, int] = {}
    for s in usable:
        if s.get("cue"):
            cues[s["cue"]] = cues.get(s["cue"], 0) + 1
    centres = [s["center"] for s in seen if isinstance(s.get("center"), list) and len(s["center"]) == 2]
    moves = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(centres, centres[1:])]
    movement = sum(moves) / len(moves) if moves else 0.0
    return {
        "samples": len(usable),
        "facing_ratio": round(sum(1 for s in seen if s.get("facing")) / len(usable), 3),
        "framed_ratio": round(sum(1 for s in seen if s.get("framing") == "ok") / len(usable), 3),
        "cues": cues,
        "steadiness": "fidgety" if movement > 0.06 else "a little restless" if movement > 0.03 else "steady",
    }
