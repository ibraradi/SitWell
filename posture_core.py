"""
Posture detection core for SitWell.

Pure computer-vision logic with NO UI dependencies (only opencv + numpy), so it
can be exercised headlessly.

Face detection prefers OpenCV's YuNet DNN model (a small, on-device face
detector that returns a confidence score and 5 facial landmarks) and falls back
to the bundled Haar cascade if the model file is missing. From the face we
derive two resolution-independent, occlusion-robust metrics:

  * cy  - vertical position of the head (eye line) in the frame (0=top, 1=bottom).
          Slouching drops the head, so cy rises.
  * fh  - a "closeness" proxy: inter-ocular distance as a fraction of the frame
          (or box height for Haar). Leaning toward the screen makes it rise.

You register good posture once (a baseline cy/fh), and the engine then reports
whether the current reading has drifted past your tolerance. Unreliable frames
(low confidence, or physically-impossible jumps from a hand crossing the face)
are rejected rather than allowed to trigger a false nudge.
"""

import json
import os
from collections import deque

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "posture_config.json")

# Bundled with the opencv-python wheel - always present.
CASCADE_PATH = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
# Optional on-device DNN detector (downloaded once); preferred when present.
YUNET_PATH = os.path.join(HERE, "face_detection_yunet_2023mar.onnx")

# A confident YuNet detection scores well above this; hands-on-face / partial
# occlusion scores below it and is discarded.
YUNET_SCORE = 0.75
# A face cannot physically jump this much of the frame between consecutive
# frames - such a reading is a detector glitch, not a posture change.
JUMP_LIMIT = 0.20
MAX_REJECTS = 8  # ~0.25s at 30fps; after this we accept reality (real fast move)


class PostureEngine:
    def __init__(self, smoothing=8):
        self.detector_kind = None
        self._yunet = None
        self._haar = None
        self._yunet_size = None
        self._init_detector()

        # Smoothing buffers so a single jittery detection never triggers a nudge.
        self._cy_buf = deque(maxlen=smoothing)
        self._fh_buf = deque(maxlen=smoothing)
        self._rejects = 0

        # Calibrated baseline (good posture).
        self.base_cy = None
        self.base_fh = None
        self.sensitivity = 5  # 1..10; higher = smaller tolerated drift

        # App-level preferences (persisted alongside the baseline).
        self.camera_index = 1     # Logitech C615 confirmed at index 1
        self.sound_enabled = True
        self.hold_seconds = 3.0   # how long a slouch must persist before a nudge

        self.load_config()

    # ----- detector setup -----------------------------------------------------
    def _init_detector(self):
        if os.path.exists(YUNET_PATH):
            try:
                # Input size is set per-frame in detect(); start with a default.
                self._yunet = cv2.FaceDetectorYN.create(
                    YUNET_PATH, "", (320, 320), YUNET_SCORE, 0.3, 5000
                )
                self.detector_kind = "yunet"
                return
            except Exception:
                self._yunet = None
        self._haar = cv2.CascadeClassifier(CASCADE_PATH)
        if self._haar.empty():
            raise RuntimeError("Could not load any face detector.")
        self.detector_kind = "haar"

    # ----- config persistence -------------------------------------------------
    def load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return
        self.sensitivity = int(data.get("sensitivity", 5))
        self.camera_index = int(data.get("camera_index", self.camera_index))
        self.sound_enabled = bool(data.get("sound_enabled", self.sound_enabled))
        self.hold_seconds = float(data.get("hold_seconds", self.hold_seconds))
        # A baseline is only valid for the detector that produced it - the two
        # detectors use different metric scales.
        if data.get("detector") == self.detector_kind:
            self.base_cy = data.get("base_cy")
            self.base_fh = data.get("base_fh")

    def save_config(self):
        data = {
            "detector": self.detector_kind,
            "base_cy": self.base_cy,
            "base_fh": self.base_fh,
            "sensitivity": self.sensitivity,
            "camera_index": self.camera_index,
            "sound_enabled": self.sound_enabled,
            "hold_seconds": self.hold_seconds,
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    @property
    def is_calibrated(self):
        return self.base_cy is not None and self.base_fh is not None

    # ----- detection ----------------------------------------------------------
    def _detect_yunet(self, frame):
        """Return (box, score, landmarks_dict) for the best face, or None."""
        h, w = frame.shape[:2]
        if self._yunet_size != (w, h):
            self._yunet.setInputSize((w, h))
            self._yunet_size = (w, h)
        _, faces = self._yunet.detect(frame)
        if faces is None or len(faces) == 0:
            return None
        # Pick the highest-confidence face.
        best = max(faces, key=lambda f: f[14])
        score = float(best[14])
        if score < YUNET_SCORE:
            return None
        box = tuple(int(v) for v in best[0:4])  # x, y, w, h
        lms = {
            "right_eye": (best[4], best[5]),
            "left_eye": (best[6], best[7]),
            "nose": (best[8], best[9]),
        }
        return box, score, lms

    def _detect_haar(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self._haar.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=6, minSize=(80, 80)
        )
        if len(faces) == 0:
            return None
        box = max(faces, key=lambda b: b[2] * b[3])
        return tuple(int(v) for v in box), 1.0, None

    def detect_face(self, frame):
        if self.detector_kind == "yunet":
            return self._detect_yunet(frame)
        return self._detect_haar(frame)

    # ----- per-frame analysis -------------------------------------------------
    def _raw_metrics(self, frame, det):
        """Compute (cy, fh) from a detection. Uses landmarks when available."""
        h, w = frame.shape[:2]
        box, _score, lms = det
        if lms is not None:
            re, le = lms["right_eye"], lms["left_eye"]
            eye_mid_y = (re[1] + le[1]) / 2.0
            cy = eye_mid_y / h
            iod = float(np.hypot(le[0] - re[0], le[1] - re[1]))  # inter-ocular dist
            fh = iod / h
        else:
            x, y, bw, bh = box
            cy = (y + bh / 2.0) / h
            fh = bh / float(h)
        return cy, fh

    def measure(self, frame):
        """Update smoothing buffers from a frame.

        Returns (cy, fh, box) with smoothed normalised metrics, or
        (None, None, None) when no reliable face is visible.
        """
        det = self.detect_face(frame)
        if det is None:
            return None, None, None
        box = det[0]

        raw_cy, raw_fh = self._raw_metrics(frame, det)

        # Reject physically-impossible jumps (e.g. a hand crossing the face)
        # unless they persist, in which case it's a real, fast movement.
        if self._cy_buf:
            mean_cy = float(np.mean(self._cy_buf))
            if abs(raw_cy - mean_cy) > JUMP_LIMIT and self._rejects < MAX_REJECTS:
                self._rejects += 1
                return mean_cy, float(np.mean(self._fh_buf)), box
        self._rejects = 0

        self._cy_buf.append(raw_cy)
        self._fh_buf.append(raw_fh)
        return float(np.mean(self._cy_buf)), float(np.mean(self._fh_buf)), box

    def reset_smoothing(self):
        self._cy_buf.clear()
        self._fh_buf.clear()
        self._rejects = 0

    # ----- calibration & verdict ----------------------------------------------
    def calibrate(self, cy, fh):
        self.base_cy = cy
        self.base_fh = fh
        self.save_config()

    def thresholds(self):
        """Map sensitivity (1..10) to (drop, lean) tolerances."""
        s = self.sensitivity
        drop_thresh = 0.11 - 0.009 * s   # frame fractions: 0.10 (s1) -> 0.02 (s10)
        lean_thresh = 0.34 - 0.026 * s   # closeness growth: 0.31 (s1) -> 0.08 (s10)
        return max(0.015, drop_thresh), max(0.06, lean_thresh)

    def evaluate(self, cy, fh):
        """Return (is_slouching, reason, severity 0..1)."""
        if not self.is_calibrated or cy is None:
            return False, "", 0.0

        drop_thresh, lean_thresh = self.thresholds()
        drop = cy - self.base_cy                 # head moved down
        lean = (fh / self.base_fh) - 1.0          # leaned toward screen

        drop_ratio = drop / drop_thresh if drop_thresh else 0
        lean_ratio = lean / lean_thresh if lean_thresh else 0
        severity = max(drop_ratio, lean_ratio)

        if drop_ratio >= 1.0 and drop_ratio >= lean_ratio:
            return True, "Head has dropped - lift your chin", min(severity, 2.0) / 2.0
        if lean_ratio >= 1.0:
            return True, "Leaning into the screen - sit back", min(severity, 2.0) / 2.0
        return False, "", min(max(severity, 0.0), 1.0)
