"""Live word recognition for the collection page: classify each take right after it ends.

The take is cut exactly as build_dataset.py cuts training clips (0.3 s before the prompt to 0.3 s
after it ends), but from a window of the session so far: a few seconds of context before the take
let the band-pass filters settle and give the frame grid (direct path at bin 250).
"""
import sys
import threading
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_dataset import BANDS, FRAME, FS, PAD_S, bandpass_all, chirp, decode_pdm, grid_start, session_profiles_fn  # noqa: E402,F401
from train_digits import fit_window, model  # noqa: E402

CONTEXT_S = 6.0  # seconds of audio before the take used for filter settling and the frame grid


def decode(words):
    w = np.asarray(words)
    return np.stack([decode_pdm(np.ascontiguousarray(w[1::2])), decode_pdm(np.ascontiguousarray(w[0::2]))])


def session_grid(words):
    """Frame grid exactly as build_dataset: from the first 30 s of the session (or all so far)."""
    audio = decode(words[:4 * 30 * FS])
    return grid_start(bandpass_all(audio), [chirp(*BANDS[0]), chirp(*BANDS[1])])


def take_features(words, start, end, grid, mode="enhanced"):
    """words: interleaved session words so far; start/end: take bounds in 100 kHz samples;
    grid: session frame grid from session_grid()."""
    s0 = max(0, start - int(PAD_S * FS))
    s1 = end + int(PAD_S * FS)
    w0 = max(0, s0 - int(CONTEXT_S * FS))
    audio = decode(words[4 * w0:4 * s1])  # 4 interleaved words per 100 kHz sample (2 mics x 2)
    feats, _ = session_profiles_fn(audio, start=(grid - w0) % FRAME)
    return feats(s0 - w0, min(audio.shape[1] - 500, s1 - w0), mode)


class Recognizer:
    def __init__(self, path):
        ck = torch.load(path, map_location="cpu", weights_only=False)
        self.classes, self.scale, self.mode = ck["classes"], ck["scale"], ck["mode"]
        self.net = model(len(self.classes))
        self.net.load_state_dict(ck["state"])
        self.net.eval()
        self.name = Path(path).name
        self.trained_on = ck.get("sessions", [])
        self.lock = threading.Lock()

    def predict(self, words, start, end, grid):
        x = take_features(words, start, end, grid, self.mode).astype(np.float32)
        x = fit_window(x / self.scale)[None]
        with self.lock, torch.no_grad():
            p = torch.softmax(self.net(torch.from_numpy(x)), 1)[0].numpy()
        order = np.argsort(p)[::-1]
        return dict(word=self.classes[order[0]], conf=float(p[order[0]]),
                    top3=[(self.classes[i], round(float(p[i]), 3)) for i in order[:3]])
