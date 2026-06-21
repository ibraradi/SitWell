"""
SitWell - friendly posture Coach.

Watches your webcam with on-device computer vision (OpenCV YuNet face
detection), learns your good posture once, then gives a gentle nudge when you
start to slouch. No wearables, no cloud, no LLM - everything runs locally and
nothing leaves your machine.

Run:  python posture_coach.py
"""

import time
import tkinter as tk

import cv2
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

import sound
from posture_core import PostureEngine

try:
    import pystray
except Exception:  # pragma: no cover - tray is optional
    pystray = None

# ---- palette ---------------------------------------------------------------
BG = "#0e1117"
CARD = "#161b22"
CARD2 = "#1c2330"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED = "#ef4444"
BLUE = "#3b82f6"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ============================================================================
# Friendly coach mascot - a little face drawn on a canvas that reacts to state.
# ============================================================================
class CoachFace(tk.Canvas):
    def __init__(self, master, size=160):
        super().__init__(master, width=size, height=size, bg=CARD,
                         highlightthickness=0, bd=0)
        self.size = size
        self.mood = None
        self.accent = GREEN
        self.draw("sleep", GREEN)

    def draw(self, mood, accent):
        self.mood = mood
        self.accent = accent
        s = self.size
        self.delete("all")
        cx, cy = s / 2, s / 2 + 6
        r = s * 0.34

        # Antenna.
        self.create_line(cx, cy - r, cx, cy - r - 16, fill=accent, width=3)
        self.create_oval(cx - 5, cy - r - 22, cx + 5, cy - r - 12,
                         fill=accent, outline="")

        # Head: glowing ring + face plate.
        self.create_oval(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4,
                         outline=accent, width=3)
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                         fill="#0b1220", outline="")

        ex = r * 0.42   # eye x-offset
        ey = cy - r * 0.18
        eye_r = r * 0.16

        if mood == "sleep":
            for sx in (-ex, ex):
                self.create_line(cx + sx - eye_r, ey, cx + sx + eye_r, ey,
                                 fill=accent, width=3)
            self.create_text(cx + r * 0.5, cy - r * 0.55, text="z",
                             fill=MUTED, font=("Segoe UI", 12, "bold"))
            self._mouth(cx, cy + r * 0.35, r * 0.28, "flat", accent)
        elif mood == "happy":
            for sx in (-ex, ex):
                self.create_oval(cx + sx - eye_r, ey - eye_r,
                                 cx + sx + eye_r, ey + eye_r,
                                 fill=accent, outline="")
            self._mouth(cx, cy + r * 0.22, r * 0.34, "smile", accent)
        elif mood == "concerned":
            for sx in (-ex, ex):
                self.create_oval(cx + sx - eye_r, ey - eye_r,
                                 cx + sx + eye_r, ey + eye_r,
                                 fill=accent, outline="")
            self._mouth(cx, cy + r * 0.4, r * 0.3, "flat", accent)
        elif mood == "alert":
            for sx in (-ex, ex):
                self.create_oval(cx + sx - eye_r * 1.25, ey - eye_r * 1.25,
                                 cx + sx + eye_r * 1.25, ey + eye_r * 1.25,
                                 fill=accent, outline="")
            self._mouth(cx, cy + r * 0.45, r * 0.22, "frown", accent)
        else:  # blink
            for sx in (-ex, ex):
                self.create_line(cx + sx - eye_r, ey, cx + sx + eye_r, ey,
                                 fill=accent, width=3)
            self._mouth(cx, cy + r * 0.22, r * 0.34, "smile", accent)

    def _mouth(self, cx, cy, w, kind, accent):
        if kind == "smile":
            self.create_arc(cx - w, cy - w, cx + w, cy + w, start=200, extent=140,
                            style="arc", outline=accent, width=3)
        elif kind == "frown":
            self.create_arc(cx - w, cy, cx + w, cy + 2 * w, start=20, extent=140,
                            style="arc", outline=accent, width=3)
        else:  # flat
            self.create_line(cx - w, cy, cx + w, cy, fill=accent, width=3)


# ============================================================================
# Posture timeline - a horizontal strip that paints the whole session as green
# (good) / red (slouching) / grey (no face or paused) buckets over time.
# ============================================================================
class TimelineBar(tk.Canvas):
    COLORS = {"good": GREEN, "bad": RED, "none": "#6b7280", "idle": "#30363d"}

    def __init__(self, master, height=30):
        super().__init__(master, height=height, bg=CARD2,
                         highlightthickness=0, bd=0)
        self.history = []
        self.bind("<Configure>", lambda e: self.redraw())

    def set_history(self, history):
        self.history = history
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        self.create_rectangle(0, 0, w, h, fill=CARD2, outline="")
        n = len(self.history)
        if n == 0:
            self.create_text(w // 2, h // 2,
                             text="No data yet - press ② Start watching",
                             fill=MUTED, font=("Segoe UI", 9))
            return
        # Map each pixel column to a time bucket so the whole session always fits.
        for x in range(w):
            idx = min(n - 1, int(x * n / w))
            self.create_line(x, 3, x, h - 3,
                             fill=self.COLORS.get(self.history[idx], CARD2))


# ============================================================================
# Gentle toast nudge - a soft, frameless overlay that fades in near the top of
# the screen and fades away. Never steals focus.
# ============================================================================
class ToastNudge:
    def __init__(self, root):
        self.root = root
        self._win = None
        self._job = None

    def show(self, message, accent=AMBER, duration=2600):
        self.dismiss()
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        win.configure(bg=accent)

        inner = tk.Frame(win, bg=CARD2, bd=0)
        inner.pack(padx=2, pady=2, fill="both", expand=True)
        tk.Label(inner, text="🤖", bg=CARD2, fg=accent,
                 font=("Segoe UI Emoji", 22)).pack(side="left", padx=(16, 10), pady=14)
        tk.Label(inner, text=message, bg=CARD2, fg=TEXT,
                 font=("Segoe UI Semibold", 13)).pack(side="left", padx=(0, 20), pady=14)

        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        sw = win.winfo_screenwidth()
        win.geometry("+%d+%d" % ((sw - w) // 2, 70))
        self._win = win
        self._fade(0.0, +0.12, duration)

    def _fade(self, alpha, step, duration):
        if self._win is None:
            return
        alpha = max(0.0, min(0.96, alpha + step))
        try:
            self._win.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if step > 0 and alpha >= 0.96:
            # Hold, then fade out.
            self._job = self.root.after(duration, lambda: self._fade(alpha, -0.10, 0))
        elif step < 0 and alpha <= 0.0:
            self.dismiss()
        else:
            self._job = self.root.after(20, lambda: self._fade(alpha, step, duration))

    def dismiss(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


# ============================================================================
# Main application.
# ============================================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.engine = PostureEngine()
        self.toast = ToastNudge(self)

        self.cap = None
        self.running = False
        self.monitoring = False
        self.calibrating = False
        self._calib_start = 0.0
        self._calib_cy = []
        self._calib_fh = []

        # Nudge timing.
        self.slouch_since = None
        self.last_nudge = 0.0
        self.nudge_cooldown = 8.0

        # Session stats.
        self.upright_sec = 0.0
        self.slouch_sec = 0.0
        self.away_sec = 0.0
        self.nudges = 0
        self.good_streak_start = time.time()
        self._last_tick = time.time()

        # Posture timeline: one state sample per second, capped at ~2 hours.
        self.history = []
        self._cur_state = "idle"
        self._last_sample = time.time()
        self._max_history = 7200

        # Mascot animation.
        self._face_mood = None
        self._next_blink = time.time() + 4

        # System tray (background running).
        self._tray = None
        self._hidden = False
        self._tray_notified = False

        self._build_ui()
        self.open_camera()
        self._setup_tray()
        # Both the close (X) and minimize buttons tuck SitWell into the tray so
        # it keeps watching in the background. Quit via the tray menu.
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Unmap>", self._on_unmap)
        self.after(30, self.update_loop)

    # ----- UI construction ----------------------------------------------------
    def _build_ui(self):
        self.title("SitWell - Posture Coach")
        self.configure(fg_color=BG)
        self.geometry("1000x660")
        self.minsize(1000, 660)

        # Header.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(18, 6))
        ctk.CTkLabel(header, text="🤖  SitWell",
                     font=("Segoe UI Semibold", 26), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(header, text="   Sit straight, feel better.",
                     font=("Segoe UI", 14), text_color=MUTED).pack(side="left", pady=(8, 0))
        self.dot = ctk.CTkLabel(header, text="●  idle", font=("Segoe UI Semibold", 13),
                                text_color=MUTED)
        self.dot.pack(side="right", pady=(6, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(6, 8))

        # --- left: camera card ---
        cam_card = ctk.CTkFrame(body, fg_color=CARD, corner_radius=16)
        cam_card.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self.video = tk.Label(cam_card, bg=CARD, bd=0)
        self.video.pack(padx=14, pady=14)
        self.privacy = ctk.CTkLabel(
            cam_card, text="🔒  On-device only · nothing is recorded or uploaded",
            font=("Segoe UI", 11), text_color=MUTED)
        self.privacy.pack(pady=(0, 8))

        # --- session statistics panel (fills the space below the camera) ---
        stats_panel = ctk.CTkFrame(cam_card, fg_color=CARD2, corner_radius=14)
        stats_panel.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        title_row = ctk.CTkFrame(stats_panel, fg_color="transparent")
        title_row.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(title_row, text="Posture timeline",
                     font=("Segoe UI Semibold", 13), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(title_row, text="this session",
                     font=("Segoe UI", 11), text_color=MUTED).pack(side="right")

        self.timeline = TimelineBar(stats_panel, height=30)
        self.timeline.pack(fill="x", padx=16, pady=(2, 2))

        axis = ctk.CTkFrame(stats_panel, fg_color="transparent")
        axis.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(axis, text="session start", font=("Segoe UI", 9),
                     text_color=MUTED).pack(side="left")
        ctk.CTkLabel(axis, text="now", font=("Segoe UI", 9),
                     text_color=MUTED).pack(side="right")

        self.summary = ctk.CTkLabel(
            stats_panel, text="You've been sitting well this session. Keep it up!",
            font=("Segoe UI Semibold", 14), text_color=TEXT, wraplength=560,
            justify="left", anchor="w")
        self.summary.pack(fill="x", padx=16, pady=(2, 8))

        legend = ctk.CTkFrame(stats_panel, fg_color="transparent")
        legend.pack(fill="x", padx=16, pady=(0, 14))
        self.lbl_good = self._legend(legend, GREEN, "Good posture", "0s")
        self.lbl_bad = self._legend(legend, RED, "Slouching", "0s")
        self.lbl_noface = self._legend(legend, "#6b7280", "Away", "0s")

        # --- right: control column ---
        right = ctk.CTkFrame(body, fg_color="transparent", width=330)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        coach_card = ctk.CTkFrame(right, fg_color=CARD, corner_radius=16)
        coach_card.pack(fill="x")
        self.face = CoachFace(coach_card, size=150)
        self.face.pack(pady=(16, 4))
        self.status = ctk.CTkLabel(coach_card, text="Booting up…",
                                   font=("Segoe UI Semibold", 15), text_color=TEXT,
                                   wraplength=290)
        self.status.pack(pady=(0, 6))
        self.substatus = ctk.CTkLabel(coach_card, text="",
                                      font=("Segoe UI", 12), text_color=MUTED,
                                      wraplength=290)
        self.substatus.pack(pady=(0, 14))

        # Stats row.
        stats = ctk.CTkFrame(right, fg_color="transparent")
        stats.pack(fill="x", pady=(12, 4))
        self.stat_upright = self._stat(stats, 0, "Upright", "0m", GREEN)
        self.stat_score = self._stat(stats, 1, "Posture", "-", BLUE)
        self.stat_nudges = self._stat(stats, 2, "Nudges", "0", AMBER)

        # Primary buttons.
        self.calib_btn = ctk.CTkButton(
            right, text="①  Register good posture", height=44,
            font=("Segoe UI Semibold", 14), fg_color=BLUE, hover_color="#2563eb",
            command=self.begin_calibration)
        self.calib_btn.pack(fill="x", pady=(14, 8))
        self.monitor_btn = ctk.CTkButton(
            right, text="②  Start watching", height=44,
            font=("Segoe UI Semibold", 14), fg_color=GREEN, hover_color="#16a34a",
            text_color="#06240f", command=self.toggle_monitor, state="disabled")
        self.monitor_btn.pack(fill="x", pady=(0, 12))

        # Settings.
        settings = ctk.CTkFrame(right, fg_color=CARD, corner_radius=16)
        settings.pack(fill="x")
        ctk.CTkLabel(settings, text="Settings", font=("Segoe UI Semibold", 13),
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(12, 2))

        row = ctk.CTkFrame(settings, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(4, 2))
        ctk.CTkLabel(row, text="Sensitivity", font=("Segoe UI", 12),
                     text_color=TEXT).pack(side="left")
        self.sens_val = ctk.CTkLabel(row, text=str(self.engine.sensitivity),
                                     font=("Segoe UI", 12), text_color=MUTED)
        self.sens_val.pack(side="right")
        self.sens = ctk.CTkSlider(settings, from_=1, to=10, number_of_steps=9,
                                  command=self.on_sensitivity)
        self.sens.set(self.engine.sensitivity)
        self.sens.pack(fill="x", padx=16, pady=(0, 8))

        cam_row = ctk.CTkFrame(settings, fg_color="transparent")
        cam_row.pack(fill="x", padx=16, pady=(2, 8))
        ctk.CTkLabel(cam_row, text="Camera", font=("Segoe UI", 12),
                     text_color=TEXT).pack(side="left")
        self.cam_menu = ctk.CTkOptionMenu(
            cam_row, values=["Camera 0", "Camera 1", "Camera 2"],
            width=130, command=self.on_camera_change)
        self.cam_menu.set("Camera %d" % self.engine.camera_index)
        self.cam_menu.pack(side="right")

        self.sound_sw = ctk.CTkSwitch(settings, text="Gentle chime on nudge",
                                      font=("Segoe UI", 12),
                                      command=self.on_sound_toggle)
        if self.engine.sound_enabled:
            self.sound_sw.select()
        self.sound_sw.pack(anchor="w", padx=16, pady=(2, 14))

        if self.engine.is_calibrated:
            self.monitor_btn.configure(state="normal")

    def _stat(self, parent, col, label, value, color):
        parent.grid_columnconfigure(col, weight=1)
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12)
        card.grid(row=0, column=col, padx=4, sticky="ew")
        val = ctk.CTkLabel(card, text=value, font=("Segoe UI Semibold", 20),
                           text_color=color)
        val.pack(pady=(12, 0))
        ctk.CTkLabel(card, text=label, font=("Segoe UI", 11),
                     text_color=MUTED).pack(pady=(0, 12))
        return val

    def _legend(self, parent, color, label, value):
        """A 'dot label: value' chip used in the timeline legend."""
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.pack(side="left", expand=True, fill="x")
        ctk.CTkLabel(cell, text="●", font=("Segoe UI", 12),
                     text_color=color).pack(side="left")
        ctk.CTkLabel(cell, text=" " + label + ":", font=("Segoe UI", 11),
                     text_color=MUTED).pack(side="left")
        val = ctk.CTkLabel(cell, text=" " + value, font=("Segoe UI Semibold", 11),
                           text_color=TEXT)
        val.pack(side="left")
        return val

    @staticmethod
    def _fmt(sec):
        sec = int(sec)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h:
            return "%dh %dm" % (h, m)
        if m:
            return "%dm %ds" % (m, s)
        return "%ds" % s

    # ----- camera -------------------------------------------------------------
    def open_camera(self):
        if self.cap is not None:
            self.cap.release()
        idx = self.engine.camera_index
        self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            self.running = False
            self._set_status("Couldn't open Camera %d." % idx,
                             "Pick another camera in Settings.", RED, "alert")
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.engine.reset_smoothing()
        self.running = True

    # ----- settings callbacks -------------------------------------------------
    def on_sensitivity(self, val):
        self.engine.sensitivity = int(round(val))
        self.sens_val.configure(text=str(self.engine.sensitivity))
        self.engine.save_config()

    def on_camera_change(self, choice):
        self.engine.camera_index = int(choice.split()[-1])
        self.engine.save_config()
        self.open_camera()

    def on_sound_toggle(self):
        self.engine.sound_enabled = bool(self.sound_sw.get())
        self.engine.save_config()

    # ----- actions ------------------------------------------------------------
    def begin_calibration(self):
        if not self.running:
            return
        self.calibrating = True
        self.monitoring = False
        self.monitor_btn.configure(text="②  Start watching", fg_color=GREEN)
        self.engine.reset_smoothing()
        self._calib_start = time.time()
        self._calib_cy.clear()
        self._calib_fh.clear()

    def toggle_monitor(self):
        if not self.engine.is_calibrated:
            return
        self.monitoring = not self.monitoring
        if self.monitoring:
            self.slouch_since = None
            self.good_streak_start = time.time()
            self._last_tick = time.time()
            self.monitor_btn.configure(text="⏸  Pause watching", fg_color=AMBER,
                                       text_color="#3b2705")
        else:
            self.monitor_btn.configure(text="②  Start watching", fg_color=GREEN,
                                       text_color="#06240f")
            self.toast.dismiss()

    # ----- main loop ----------------------------------------------------------
    def update_loop(self):
        if self.running and self.cap is not None:
            ok, frame = self.cap.read()
            if ok:
                frame = cv2.flip(frame, 1)
                self._process(frame)
        self.after(30, self.update_loop)

    def _process(self, frame):
        cy, fh, box = self.engine.measure(frame)
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now

        box_color = (90, 200, 120)
        label = ""

        if self.calibrating:
            self._do_calibration(cy, fh, now)
            box_color = (245, 180, 60)
            self._cur_state = "idle"
        elif self.monitoring and self.engine.is_calibrated:
            box_color, label = self._do_monitoring(cy, fh, now, dt)
        else:
            self._set_status("Ready when you are.",
                             "Press ① to register your good posture.", BLUE, "happy")
            box_color = (120, 140, 160)
            self._cur_state = "idle"

        self._sample_history(now)
        self._draw_and_show(frame, box, box_color, label)
        self._update_stats()
        self._maybe_blink(now)

    def _sample_history(self, now):
        """Record one posture-state bucket per second for the timeline."""
        if now - self._last_sample < 1.0:
            return
        self._last_sample = now
        self.history.append(self._cur_state)
        if len(self.history) > self._max_history:
            self.history.pop(0)
        self.timeline.set_history(self.history)

    def _do_calibration(self, cy, fh, now):
        if cy is not None:
            self._calib_cy.append(cy)
            self._calib_fh.append(fh)
        remaining = max(0.0, 2.5 - (now - self._calib_start))
        self._set_status("Hold your best upright posture…",
                         "Capturing… %.1fs   (keep your face in view)" % remaining,
                         AMBER, "concerned")
        if now - self._calib_start >= 2.5:
            self.calibrating = False
            if len(self._calib_cy) < 5:
                self._set_status("Hmm, I couldn't see you clearly.",
                                 "Face the camera and try ① again.", RED, "alert")
                return
            import numpy as np
            self.engine.calibrate(float(np.median(self._calib_cy)),
                                  float(np.median(self._calib_fh)))
            self.monitor_btn.configure(state="normal")
            self._set_status("✓ Good posture registered!",
                             "Press ② and I'll start watching.", GREEN, "happy")

    def _do_monitoring(self, cy, fh, now, dt):
        slouching, reason, severity = self.engine.evaluate(cy, fh)

        if cy is None:
            self.slouch_since = None
            self.away_sec += dt
            self._cur_state = "none"
            self._set_status("I can't see you right now.",
                             "Sit back into the camera's view.", MUTED, "sleep")
            return (120, 120, 120), ""

        if not slouching:
            if self.slouch_since is not None:
                # Just recovered.
                self.slouch_since = None
                self.good_streak_start = now
                if self.engine.sound_enabled:
                    sound.play_good()
            self.upright_sec += dt
            self._cur_state = "good"
            mins = (now - self.good_streak_start) / 60.0
            sub = ("Nice - %d min upright in a row 🌿" % int(mins)
                   if mins >= 1 else "Keep it up!")
            self._set_status("Great posture 🧘", sub, GREEN, "happy")
            return (90, 200, 120), "Good posture"

        # Slouching.
        self.slouch_sec += dt
        self._cur_state = "bad"
        if self.slouch_since is None:
            self.slouch_since = now
        held = now - self.slouch_since

        if held < self.engine.hold_seconds:
            self._set_status("Easing into a slouch…", reason, AMBER, "concerned")
            return (245, 180, 60), reason

        # Sustained slouch → gentle nudge.
        self._set_status("Gentle nudge 🪑", reason, RED, "alert")
        if now - self.last_nudge >= self.nudge_cooldown:
            self.last_nudge = now
            self.nudges += 1
            self.toast.show(reason, accent=AMBER)
            if self.engine.sound_enabled:
                sound.play_nudge()
        return (70, 70, 235), reason

    # ----- system tray --------------------------------------------------------
    def _make_tray_image(self, s=64):
        """Draw a small SitWell robot face for the tray icon."""
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        blue = (59, 130, 246, 255)
        d.line([s // 2, 13, s // 2, 5], fill=blue, width=3)       # antenna
        d.ellipse([s // 2 - 3, 2, s // 2 + 3, 8], fill=blue)      # antenna tip
        d.ellipse([10, 13, s - 10, s - 8], outline=blue, width=4)  # head
        d.ellipse([22, 28, 29, 35], fill=blue)                    # left eye
        d.ellipse([35, 28, 42, 35], fill=blue)                    # right eye
        d.arc([22, 30, 42, 48], start=20, end=160, fill=blue, width=3)  # smile
        return img

    def _setup_tray(self):
        if pystray is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open SitWell", self._tray_open, default=True),
            pystray.MenuItem(
                lambda i: "Resume watching" if not self.monitoring else "Pause watching",
                self._tray_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit SitWell", self._tray_quit),
        )
        self._tray = pystray.Icon(
            "SitWell", self._make_tray_image(), "SitWell - Posture Coach", menu)
        self._tray.run_detached()

    # Tray callbacks run on pystray's thread → marshal back onto the Tk thread.
    def _tray_open(self, icon=None, item=None):
        self.after(0, self.show_window)

    def _tray_toggle(self, icon=None, item=None):
        self.after(0, self.toggle_monitor)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self.on_close)

    def _on_unmap(self, event):
        # Fired when the user clicks the minimize button.
        if event.widget is self and self.state() == "iconic":
            self.hide_to_tray()

    def hide_to_tray(self):
        if self._tray is None:
            # No tray available - fall back to a normal minimize.
            self.iconify()
            return
        self._hidden = True
        self.withdraw()
        if not self._tray_notified:
            self._tray_notified = True
            try:
                self._tray.notify(
                    "Still watching in the background. "
                    "Right-click the tray icon for options.", "SitWell")
            except Exception:
                pass

    def show_window(self):
        self._hidden = False
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    # ----- rendering ----------------------------------------------------------
    def _draw_and_show(self, frame, box, color, label):
        if self._hidden:
            return  # window is in the tray; skip the costly image conversion
        if box is not None:
            x, y, w, h = box
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            if label:
                cv2.putText(frame, label, (x, max(22, y - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        photo = ImageTk.PhotoImage(image=img)
        self.video.configure(image=photo)
        self.video.image = photo

    def _set_status(self, main, sub, color, mood):
        self.status.configure(text=main, text_color=TEXT)
        self.substatus.configure(text=sub)
        self.dot.configure(text="●  " + self._dot_word(mood), text_color=color)
        if self._face_mood != mood:
            self._face_mood = mood
            self.face.draw(mood, color)

    def _dot_word(self, mood):
        return {"happy": "good", "concerned": "watch", "alert": "slouch",
                "sleep": "no face"}.get(mood, "idle")

    def _maybe_blink(self, now):
        # Occasional friendly blink while happy.
        if self._face_mood == "happy" and now >= self._next_blink:
            self.face.draw("blink", self.face.accent)
            self.after(140, lambda: self.face.draw("happy", self.face.accent)
                       if self._face_mood == "happy" else None)
            self._next_blink = now + 5

    def _update_stats(self):
        m = int(self.upright_sec // 60)
        self.stat_upright.configure(text=("%dm" % m) if m else "%ds" % int(self.upright_sec))
        total = self.upright_sec + self.slouch_sec
        if total > 3:
            self.stat_score.configure(text="%d%%" % int(100 * self.upright_sec / total))
        self.stat_nudges.configure(text=str(self.nudges))

        # Timeline legend + the headline "time in bad posture" sentence.
        self.lbl_good.configure(text=" " + self._fmt(self.upright_sec))
        self.lbl_bad.configure(text=" " + self._fmt(self.slouch_sec))
        self.lbl_noface.configure(text=" " + self._fmt(self.away_sec))
        if self.slouch_sec < 1:
            self.summary.configure(
                text="You've been sitting well this session. Keep it up!",
                text_color=TEXT)
        else:
            self.summary.configure(
                text="You've spent %s in bad posture this session." % self._fmt(self.slouch_sec),
                text_color=(AMBER if self.slouch_sec < 120 else RED))

    # ----- shutdown -----------------------------------------------------------
    def on_close(self):
        self.running = False
        self.toast.dismiss()
        if self.cap is not None:
            self.cap.release()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.destroy()


def main():
    sound.ensure_sounds()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
