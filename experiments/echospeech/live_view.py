"""Real-time enhanced echo profiles for the collection page (split bands, 4 paths).

Fed raw interleaved PDM words as they arrive; keeps the last few seconds of the enhanced
differential profile (4 paths x 80 bins, crop 226:306) for the browser to draw.
Same processing as build_dataset.py: PDM -> 100 kHz (overlap-save) -> band-pass per speaker
band (streaming) -> per-frame correlation on a global frame grid (found after 2 s, as the
official code) -> 4-frame coherent average -> |.| -> 50 ms difference.
"""
import sys
import threading
from collections import deque
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, lfilter, lfilter_zi, resample_poly

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "split_bands"))
from split_capture import BANDS, BP, chirp  # noqa: E402

FS, FRAME = 100_000, 500
CROP = (226, 306)
AVG, LAG = 4, 10
KEEP = 1200  # frames shown (6 s)


class LiveView:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def frame_sample(self, i):
        """Audio sample (100 kHz, from the start of the recording) where enhanced frame i starts."""
        return self.start + (i + AVG + LAG - 1) * FRAME

    def reset(self):
        self.words = [np.zeros(0, np.uint16), np.zeros(0, np.uint16)]  # per mic: [mic1 (pin 6), mic2 (pin 8)]
        self.done = 0               # absolute words per mic already decoded
        self.base = 0               # absolute word index of self.words[m][0]
        self.debug = None           # set to [] to collect decoded audio (tests)
        self.chunk, self.ov = 20_000, 4_000
        self.filt_state = None
        self.ba = [butter(5, [BP[n][0] / (FS / 2), BP[n][1] / (FS / 2)], btype="band") for n in range(2)]
        tx = [chirp(*BANDS[0]), chirp(*BANDS[1])]
        self.tx = tx
        self.T = [np.fft.rfft(t[::-1], 2 * FRAME) for t in tx]
        self.fbuf = np.zeros((2, 2, 0), np.float32)  # filtered samples not yet framed
        self.fpos = 0               # absolute sample index of fbuf[..., 0]
        self.start = None           # frame grid offset
        self.calib = []             # filtered samples for alignment
        self.hist = deque(maxlen=AVG + LAG + 2)  # recent signed crops (4, 80)
        self.out = deque(maxlen=KEEP)            # enhanced frames (4, 80)
        self.scale = np.full(4, 1e-6)
        self.count = 0              # enhanced frames produced so far (enhanced frame i starts at sample
                                    # start + (i + AVG + LAG - 1) * FRAME)
        self.listeners = []         # callables fed each new enhanced frame (index, frame)

    def feed(self, interleaved):
        """interleaved: uint16 words mic2, mic1, mic2, mic1, ... (as the Teensy sends them)."""
        self.words[0] = np.concatenate([self.words[0], interleaved[1::2]])
        self.words[1] = np.concatenate([self.words[1], interleaved[0::2]])
        while min(len(self.words[0]), len(self.words[1])) + self.base - self.done >= self.chunk + self.ov:
            self._decode_chunk()

    def _decode_chunk(self):
        """Overlap-save PDM decoding: decode [done-ov, done+chunk+ov), keep the middle part."""
        a = max(0, self.done - self.ov)
        b = self.done + self.chunk + self.ov
        audio = []
        for m in range(2):
            w = self.words[m][a - self.base:b - self.base]
            bits = np.unpackbits(w.astype(">u2").view(np.uint8)).astype(np.float32) * 2 - 1
            x = resample_poly(resample_poly(bits, 1, 8), 1, 4)  # 16 bits/word -> 0.5 samples/word
            lo = (self.done - a) // 2
            audio.append(x[lo:lo + self.chunk // 2])
        self.done += self.chunk
        new_base = max(0, self.done - self.ov)
        for m in range(2):
            self.words[m] = self.words[m][new_base - self.base:]
        self.base = new_base
        audio = np.stack(audio)
        if self.debug is not None:
            self.debug.append(audio)
        self._filter(audio)

    def _filter(self, audio):
        if self.filt_state is None:
            self.filt_state = [[lfilter_zi(*self.ba[n]) * 0.0 for _ in range(2)] for n in range(2)]
        f = np.zeros((2, 2, audio.shape[1]), np.float32)
        for n in range(2):
            b, a = self.ba[n]
            for c in range(2):
                f[n, c], self.filt_state[n][c] = lfilter(b, a, audio[c], zi=self.filt_state[n][c])
        if self.start is None:
            self.calib.append(f)
            total = sum(x.shape[2] for x in self.calib)
            if total >= 2 * FS:  # align on 2 s of signal, as build_dataset.py
                cal = np.concatenate(self.calib, axis=2)
                L = cal.shape[2] // FRAME * FRAME
                acc = []
                for n in range(2):
                    for c in range(2):
                        cc = fftconvolve(cal[n, c, :L], self.tx[n][::-1], mode="full")[FRAME - 1:FRAME - 1 + L]
                        acc.append(np.abs(cc).reshape(-1, FRAME))
                sp = np.median(np.concatenate(acc), axis=0)
                self.start = (int(np.argmax(sp)) + FRAME - FRAME // 2) % FRAME
                self.fbuf = cal[:, :, self.start:]
                self.fpos = self.start
                self.calib = []
                self._frames()
            return
        self.fbuf = np.concatenate([self.fbuf, f], axis=2)
        self._frames()

    def _frames(self):
        n = self.fbuf.shape[2] // FRAME
        if n == 0:
            return
        blk = self.fbuf[:, :, :n * FRAME].reshape(2, 2, n, FRAME)
        self.fbuf = self.fbuf[:, :, n * FRAME:]
        prof = np.zeros((2, 2, n, FRAME), np.float32)
        for b in range(2):
            X = np.fft.rfft(blk[b], 2 * FRAME, axis=-1)
            prof[b] = np.fft.irfft(X * self.T[b], 2 * FRAME, axis=-1)[..., FRAME - 1:2 * FRAME - 1]
        crops = prof.reshape(4, n, FRAME)[:, :, CROP[0]:CROP[1]]  # signed
        with self.lock:
            for k in range(n):
                self.hist.append(crops[:, k])
                if len(self.hist) >= AVG + LAG:
                    h = list(self.hist)
                    now = np.abs(np.mean(h[-AVG:], axis=0))
                    before = np.abs(np.mean(h[-AVG - LAG:-LAG], axis=0))
                    e = now - before
                    self.scale = 0.995 * self.scale + 0.005 * np.percentile(np.abs(e), 99, axis=1).clip(1e-9)
                    self.out.append(e)
                    for f in self.listeners:
                        f(self.count, e)
                    self.count += 1

    def snapshot(self):
        """uint8 image (4, 80, frames): 128 = no change; scaled per path by a running 99th percentile."""
        with self.lock:
            if not self.out:
                return None
            a = np.stack(self.out, axis=-1)  # (4, 80, F)
            s = self.scale.copy()
        img = np.clip(a / (2.5 * s[:, None, None]), -1, 1)
        return ((img + 1) * 127.5).astype(np.uint8)
