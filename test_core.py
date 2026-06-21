"""
Headless tests for the SitWell posture core - no GUI/Tkinter involved.

Usage:
  python test_core.py logic   # deterministic math checks, NO camera needed
  python test_core.py live    # live webcam readings + debug view (press q to quit,
                              #   c to calibrate to your current posture)
"""

import sys
import time

from posture_core import PostureEngine


def test_logic():
    """Feed synthetic cy/fh readings through evaluate() and assert verdicts."""
    eng = PostureEngine()
    eng.sensitivity = 5
    # Pretend good posture: head centred, face = 30% of frame height.
    base_cy, base_fh = 0.40, 0.30
    eng.calibrate(base_cy, base_fh)
    drop_thresh, lean_thresh = eng.thresholds()
    print("sensitivity=%d -> drop_thresh=%.3f lean_thresh=%.3f"
          % (eng.sensitivity, drop_thresh, lean_thresh))

    checks = []

    def expect(name, got, want):
        ok = got == want
        checks.append(ok)
        print(("  PASS " if ok else "  FAIL ") + "%-34s got=%s want=%s"
              % (name, got, want))

    # 1. Exactly at baseline -> good.
    s, _, _ = eng.evaluate(base_cy, base_fh)
    expect("at baseline = good", s, False)

    # 2. Tiny drift below threshold -> still good.
    s, _, _ = eng.evaluate(base_cy + drop_thresh * 0.5, base_fh)
    expect("small head drop = good", s, False)

    # 3. Head dropped past threshold -> slouch.
    s, r, _ = eng.evaluate(base_cy + drop_thresh * 1.5, base_fh)
    expect("big head drop = slouch", s, True)
    print("       reason:", r)

    # 4. Leaned in past threshold -> slouch.
    s, r, _ = eng.evaluate(base_cy, base_fh * (1 + lean_thresh * 1.5))
    expect("leaning in = slouch", s, True)
    print("       reason:", r)

    # 5. Sitting BACK (smaller face, higher head) is never a slouch.
    s, _, _ = eng.evaluate(base_cy - 0.05, base_fh * 0.8)
    expect("sitting back = good", s, False)

    # 6. Uncalibrated engine never flags.
    eng2 = PostureEngine()
    eng2.base_cy = eng2.base_fh = None
    s, _, _ = eng2.evaluate(0.9, 0.9)
    expect("uncalibrated = never slouch", s, False)

    # 7. Higher sensitivity tightens thresholds.
    eng.sensitivity = 9
    d9, l9 = eng.thresholds()
    expect("s9 drop < s5 drop", d9 < drop_thresh, True)
    expect("s9 lean < s5 lean", l9 < lean_thresh, True)

    passed = sum(checks)
    print("\n%d/%d checks passed" % (passed, len(checks)))
    return all(checks)


def test_live(cam_index=0):
    """Open the webcam and stream real posture readings to the console."""
    import cv2

    eng = PostureEngine()
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print("ERROR: could not open webcam.")
        return False
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("Live test on camera index %d. Keys:  c = calibrate   q = quit" % cam_index)
    if eng.is_calibrated:
        print("Loaded baseline: cy=%.3f fh=%.3f" % (eng.base_cy, eng.base_fh))

    last_print = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            print("frame grab failed"); break
        frame = cv2.flip(frame, 1)
        cy, fh, box = eng.measure(frame)

        verdict = "no-face"
        color = (160, 160, 160)
        if cy is not None:
            slouch, reason, sev = eng.evaluate(cy, fh)
            if not eng.is_calibrated:
                verdict = "uncalibrated (press c)"
            elif slouch:
                verdict = "SLOUCH: %s (sev %.2f)" % (reason, sev)
                color = (60, 60, 235)
            else:
                verdict = "good (margin %.2f)" % sev
                color = (90, 200, 120)

        # Throttle console output to ~2/sec.
        now = time.time()
        if now - last_print > 0.5:
            last_print = now
            if cy is None:
                print("no face detected")
            else:
                print("cy=%.3f fh=%.3f | %s" % (cy, fh, verdict))

        if box is not None:
            x, y, bw, bh = box
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
        cv2.putText(frame, verdict, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, color, 2, cv2.LINE_AA)
        cv2.imshow("SitWell core test (c=calibrate q=quit)", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("c") and cy is not None:
            eng.calibrate(cy, fh)
            print(">> calibrated: cy=%.3f fh=%.3f" % (cy, fh))

    cap.release()
    cv2.destroyAllWindows()
    return True


def test_sample(n_frames=40, cam_index=0):
    """Non-interactive: grab N real frames, report detection rate + readings.

    No GUI window, no keypresses - proves the webcam + detector work on real
    input and prints the cy/fh range so we can sanity-check the signal.
    """
    import cv2

    eng = PostureEngine()
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print("ERROR: could not open webcam.")
        return False
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Warm up - first frames are often blank while the sensor settles.
    for _ in range(5):
        cap.read()

    hits, cys, fhs = 0, [], []
    for i in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            print("frame %d: grab failed" % i)
            continue
        frame = cv2.flip(frame, 1)
        cy, fh, box = eng.measure(frame)
        if cy is not None:
            hits += 1
            cys.append(cy)
            fhs.append(fh)
        time.sleep(0.03)

    cap.release()
    print("frames=%d  face-detected=%d  rate=%.0f%%" % (n_frames, hits, 100.0 * hits / n_frames))
    if hits:
        print("cy  min/mean/max = %.3f / %.3f / %.3f" % (min(cys), sum(cys) / len(cys), max(cys)))
        print("fh  min/mean/max = %.3f / %.3f / %.3f" % (min(fhs), sum(fhs) / len(fhs), max(fhs)))
        print("\nLooks healthy if rate is high and cy/fh are stable while you hold still.")
        return True
    print("\nNo face detected in any frame - check lighting / camera framing.")
    return False


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "logic"
    cam = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if mode == "live":
        test_live(cam)
    elif mode == "sample":
        ok = test_sample(cam_index=cam)
        sys.exit(0 if ok else 1)
    else:
        ok = test_logic()
        sys.exit(0 if ok else 1)
