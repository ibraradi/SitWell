# 🤖 SitWell - Posture Coach

**Sit straight, feel better.** A friendly coach that uses your webcam and
on-device computer vision to quietly watch your posture and give you a gentle
nudge the moment you start slouching.

- **No wearables** - just your webcam.
- **No nagging** - one quiet nudge, then it waits.
- **No cloud, no LLM** - everything runs locally with OpenCV. Frames never
  leave your machine and nothing is recorded or uploaded.

---

## Features

- 🎯 **One-tap calibration** - register *your* good posture once; everything is
  measured relative to that.
- 👀 **Robust face tracking** - uses OpenCV's on-device **YuNet** detector
  (with a Haar-cascade fallback). Handles head tilt and partial occlusion
  (e.g. a hand brushing your face) without false alarms.
- 🔔 **Gentle alerts** - a soft chime and a fading on-screen toast, only after a
  slouch is *sustained* (no twitchy nudges), rate-limited so it never nags.
- 📊 **Session statistics** - a green/red **posture timeline** plus running
  totals: time sitting well, time slouching, time away, and a posture score.
- 🤖 **Friendly mascot** - a little face that reacts to your state (happy /
  concerned / alert / sleeping).
- 🗔 **Runs in the background** - minimize or close to the **system tray** and it
  keeps watching while you work. Pause / resume / quit from the tray menu.
- ⚙️ **Configurable** - sensitivity slider, camera picker, sound toggle. All
  settings persist between runs.

---

## How it works

SitWell finds your face in the webcam frame and tracks two resolution-independent
signals:

1. **How high your head sits** in the frame - slouching drops your head.
2. **How close you are** to the screen (inter-ocular distance) - leaning in makes
   your face larger.

You register your *good* posture once. From then on, if either drifts past your
tolerance for a few seconds, SitWell gives a gentle nudge. Readings are smoothed
and low-confidence/implausible frames are rejected, so a blink or a passing hand
won't trigger an alert.

---

## Installation

**Requirements:** Python 3.9+ on Windows (built and tested on **Windows 11 with
Python 3.14**). macOS/Linux should work too, but the chime and tray are
Windows-tuned.

```bash
# 1. Clone
git clone https://github.com/<your-username>/SitWell.git
cd SitWell

# 2. (Recommended) create a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python posture_coach.py
```

### Face-detection model

SitWell prefers the **YuNet** face detector. The model file
(`face_detection_yunet_2023mar.onnx`, ~232 KB, Apache-2.0, from the official
[OpenCV model zoo](https://github.com/opencv/opencv_zoo)) is included in this
repo, so there is nothing extra to download. If the file is ever missing, SitWell
automatically falls back to the Haar cascade bundled with `opencv-python`.

---

## Using it

1. **① Register good posture** - sit the way you *want* to sit, then press it and
   hold still for ~2.5 seconds. Your baseline is saved to `posture_config.json`.
2. **② Start watching** - SitWell watches quietly. Sit well and the status stays
   green; slouch for a few seconds and you get a gentle nudge.
3. **Minimize / close** - tucks SitWell into the system tray, where it keeps
   watching. Right-click the tray icon to **Open**, **Pause/Resume**, or **Quit**.
4. **Settings** - **Sensitivity** (higher = nudged on smaller slouches),
   **Camera** picker, and the **gentle chime** toggle. All persist between runs.

Your baseline and settings persist, so you only calibrate once.

---

## Project layout

| File | Purpose |
|------|---------|
| [`posture_coach.py`](posture_coach.py) | The app: UI, mascot, tray, alerts, stats. |
| [`posture_core.py`](posture_core.py) | Pure CV engine (detection + slouch verdict), no UI. |
| [`sound.py`](sound.py) | Synthesises the gentle chime cues. |
| [`test_core.py`](test_core.py) | Headless tests for the CV core (see below). |
| `face_detection_yunet_2023mar.onnx` | On-device YuNet face-detection model. |

---

## Testing

The CV core is decoupled from the UI so it can be checked headlessly:

```bash
python test_core.py logic            # deterministic slouch-math checks (no camera)
python test_core.py sample [cam]     # grab real frames, report detection rate
python test_core.py live   [cam]     # live readings + debug window (c=calibrate, q=quit)
```

`cam` is an optional camera index (default 0).

---

## Tuning

- A slouch must persist for **3 seconds** before a nudge (no twitchy alerts).
- Nudges are rate-limited to once every **8 seconds**.
- Both live near the top of `App.__init__` in
  [`posture_coach.py`](posture_coach.py); detection thresholds live in
  `PostureEngine.thresholds()` in [`posture_core.py`](posture_core.py).

---

## Privacy

Everything runs **on-device**. The webcam feed is analysed locally in memory and
is never saved, streamed, or uploaded. No accounts, no telemetry, no LLM.
