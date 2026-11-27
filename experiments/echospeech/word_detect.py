"""Find mouthed words in the live enhanced echo profiles (no key presses).

Activity = mean |enhanced difference| over the 4 paths, each path divided by its own quiet level
(a low percentile of its recent activity), smoothed over 100 ms. A word starts when activity
rises above `k_on` x quiet and ends once it has stayed below `k_off` x quiet for `hang_s`.
Each word is reported as (start_sample, end_sample) in 100 kHz samples, padded like a take:
the start is moved `lead_s` earlier (a take started with a key press just before mouthing).
"""
from collections import deque

import numpy as np

FPS = 200  # enhanced frames per second


class WordDetector:
    def __init__(self, frame_sample, on_word, k_on=2.5, k_off=1.6, hang_s=0.5, min_s=0.2, max_s=3.0,
                 lead_s=0.25, warm_s=3.0):
        self.frame_sample, self.on_word = frame_sample, on_word
        self.k_on, self.k_off = k_on, k_off
        self.hang, self.min_f, self.max_f = int(hang_s * FPS), int(min_s * FPS), int(max_s * FPS)
        self.lead, self.warm = int(lead_s * FPS), int(warm_s * FPS)
        self.raw = deque(maxlen=6 * FPS)     # per-path activity, recent 6 s (for the quiet level)
        self.smooth = deque(maxlen=20)       # 100 ms smoothing of the normalised activity
        self.quiet = None
        self.level = 0.0                     # last normalised activity (for the page's meter)
        self.state, self.t_on, self.t_low = "idle", 0, 0

    def __call__(self, i, e):
        a = np.abs(e).mean(axis=1)           # (4,) activity per path
        self.raw.append(a)
        if i % 100 == 0 and len(self.raw) >= FPS:   # refresh quiet level every 0.5 s
            r = np.array(self.raw)
            self.quiet = np.percentile(r, 30, axis=0).clip(1e-12)
        if self.quiet is None or i < self.warm:
            return
        self.smooth.append(float(np.mean(a / self.quiet)))
        lv = self.level = float(np.mean(self.smooth))
        if self.state == "manual":  # a manual (space bar) take is running
            return
        if self.state == "idle":
            if lv > self.k_on:
                self.state, self.t_on, self.t_low = "word", i, i
        else:
            if lv > self.k_off:
                self.t_low = i
            if i - self.t_low >= self.hang or i - self.t_on >= self.max_f:
                end = self.t_low if i - self.t_low >= self.hang else i
                if end - self.t_on >= self.min_f:
                    self.on_word(self.frame_sample(max(0, self.t_on - self.lead)), self.frame_sample(end))
                self.state = "idle"
