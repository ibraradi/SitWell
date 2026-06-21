"""
Gentle audio cues for SitWell.

We synthesise soft sine-wave chimes (with smooth fade in/out) once, cache them as
.wav files, and play them asynchronously. The goal is a calm "nudge", never a
harsh system beep - in keeping with "no nagging".
"""

import math
import os
import struct
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
NUDGE_WAV = os.path.join(HERE, "_chime_nudge.wav")
GOOD_WAV = os.path.join(HERE, "_chime_good.wav")

SAMPLE_RATE = 44100


def _write_chime(path, notes, volume=0.35):
    """Render a sequence of (frequency_hz, seconds) notes to a 16-bit WAV.

    Each note gets a raised-cosine envelope so there are no clicks, and notes
    overlap slightly to sound like a soft mallet chime rather than discrete beeps.
    """
    frames = bytearray()
    for freq, dur in notes:
        n = int(SAMPLE_RATE * dur)
        for i in range(n):
            t = i / SAMPLE_RATE
            # Raised-cosine envelope: gentle attack and long release.
            env = 0.5 - 0.5 * math.cos(2 * math.pi * min(i, n - i) / n)
            # A touch of a higher harmonic gives it a "bell" warmth.
            sample = math.sin(2 * math.pi * freq * t)
            sample += 0.25 * math.sin(2 * math.pi * freq * 2 * t)
            val = int(max(-1.0, min(1.0, sample / 1.25)) * env * volume * 32767)
            frames += struct.pack("<h", val)

    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))


def ensure_sounds():
    """Generate the cached chime files if they don't already exist."""
    if not os.path.exists(NUDGE_WAV):
        # Soft descending two-note "ti-dum" - noticeable but calm.
        _write_chime(NUDGE_WAV, [(659.25, 0.18), (523.25, 0.32)])
    if not os.path.exists(GOOD_WAV):
        # Light ascending "ding" for returning to good posture.
        _write_chime(GOOD_WAV, [(523.25, 0.12), (783.99, 0.22)], volume=0.25)


# --- playback (Windows winsound, with graceful fallback) ---------------------
try:
    import winsound

    def _play(path):
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass
except Exception:  # pragma: no cover - non-Windows
    def _play(path):
        print("\a", end="", flush=True)


def play_nudge():
    ensure_sounds()
    _play(NUDGE_WAV)


def play_good():
    ensure_sounds()
    _play(GOOD_WAV)


if __name__ == "__main__":
    import time
    ensure_sounds()
    print("Playing nudge chime…")
    play_nudge()
    time.sleep(1.2)
    print("Playing good chime…")
    play_good()
    time.sleep(1.2)
    print("Done. WAVs written next to this script.")
