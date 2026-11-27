#!/usr/bin/env python3
"""Turn collected sessions into training clips (EchoSpeech-style features).

    .venv/bin/python experiments/echospeech/build_dataset.py            # all sessions not yet built
    .venv/bin/python experiments/echospeech/build_dataset.py --force

For each session: PDM words -> 100 kHz audio per mic (chunked) -> band-pass per speaker band
(17.5-24.5 / 24.5-31.5 kHz) -> per-frame correlation with that speaker's chirp on one global
500-sample frame grid -> |profile| -> differential (frame minus previous frame) -> 4 paths
[s0m1, s0m2, s1m1, s1m2] -> crop bins 226:306 (direct path aligned to bin 250). Default features
are 'enhanced' (4-frame average, 50 ms difference); --mode plain gives the EchoSpeech differential.
Every non-redo prompt becomes one clip from 0.3 s before it appears to 0.3 s after it ends,
saved as float16 (4, 80, T) in captures/echospeech/<session>/clips_<mode>.npz with labels and order.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, lfilter, resample_poly

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "split_bands"))
from split_capture import BANDS, BP, chirp  # noqa: E402

FS, FRAME = 100_000, 500
CROP = (226, 306)
PAD_S = 0.3
WORDS_PER_S = 200_000  # per mic


def decode_pdm(words, chunk=2_000_000, overlap=4_000):
    """Interleaved-for-one-mic uint16 words (MSB first) -> 100 kHz float32, in overlapping chunks."""
    out = []
    n = len(words)
    for s in range(0, n, chunk):
        a, b = max(0, s - overlap), min(n, s + chunk + overlap)
        bits = np.unpackbits(words[a:b].astype(">u2").view(np.uint8)).astype(np.float32) * 2 - 1
        x = resample_poly(resample_poly(bits, 1, 8), 1, 4)  # 3.2 MHz -> 100 kHz, 16 bits/word -> 0.5 samples
        lo = (s - a) // 2
        hi = lo + (min(n, s + chunk) - s) // 2
        out.append(x[lo:hi])
    x = np.concatenate(out)
    return (x - x.mean()).astype(np.float32)


def session_profiles_fn(audio):
    """Band-pass both bands for both mics, find the global frame grid, return a function that
    gives the cropped differential features (4, 80, T) for a sample range."""
    filt = np.zeros((2, 2, audio.shape[1]), np.float32)
    for n in range(2):
        b, a = butter(5, [BP[n][0] / (FS / 2), BP[n][1] / (FS / 2)], btype="band")
        for c in range(2):
            filt[n, c] = lfilter(b, a, audio[c]).astype(np.float32)
    tx = [chirp(*BANDS[0]), chirp(*BANDS[1])]
    T = [np.fft.rfft(t[::-1], 2 * FRAME) for t in tx]
    # global frame grid from the first 30 s (median over frames, as in sonispeech_capture)
    # as the official code: correlate the continuous signal with the chirp, fold into frames,
    # take the strongest lag (median over frames, as tools/sonispeech_capture.py) and put it at 250
    from scipy.signal import fftconvolve
    L = min(filt.shape[2], 30 * FS) // FRAME * FRAME
    acc = []
    for n in range(2):
        for c in range(2):
            cc = fftconvolve(filt[n, c, :L], tx[n][::-1], mode="full")[FRAME - 1:FRAME - 1 + L]
            acc.append(np.abs(cc).reshape(-1, FRAME))
    sp = np.median(np.concatenate(acc), axis=0)
    start = (int(np.argmax(sp)) + FRAME - FRAME // 2) % FRAME

    def feats(s0, s1, mode="enhanced", avg=4, lag=10):
        """mode 'plain': frame minus previous frame (EchoSpeech / SoniSpeech differential).
        mode 'enhanced': |profile| averaged over `avg` frames, minus the frame `lag` frames earlier
        (50 ms), which raised speech contrast from about 0 to +6..+15 dB on this rig."""
        from scipy.ndimage import uniform_filter1d
        extra = 1 if mode == "plain" else lag + avg
        k0 = max(extra, (s0 - start) // FRAME)
        k1 = (s1 - start) // FRAME
        a, b = start + (k0 - extra) * FRAME, start + k1 * FRAME
        prof = np.zeros((2, 2, k1 - k0 + extra, FRAME), np.float32)
        for n in range(2):
            for c in range(2):
                X = np.fft.rfft(filt[n, c, a:b].reshape(-1, FRAME), 2 * FRAME, axis=-1)
                prof[n, c] = np.fft.irfft(X * T[n], 2 * FRAME, axis=-1)[:, FRAME - 1:2 * FRAME - 1]  # signed
        if mode == "plain":
            m = np.abs(prof)
            d = m[:, :, 1:] - m[:, :, :-1]
        else:  # coherent average of the signed profile first (playback and recording share a clock)
            m = np.abs(uniform_filter1d(prof, avg, axis=2))
            d = (m[:, :, lag:] - m[:, :, :-lag])[:, :, avg:]
        return d.reshape(4, d.shape[2], FRAME)[:, :, CROP[0]:CROP[1]].transpose(0, 2, 1)
    return feats, start


def build(sdir, mode="enhanced"):
    meta = json.load(open(sdir / "events.json"))
    words = np.load(sdir / "raw_words.npy")
    audio = np.stack([decode_pdm(np.ascontiguousarray(words[1::2])),   # mic 1 (pin 6)
                      decode_pdm(np.ascontiguousarray(words[0::2]))])  # mic 2 (pin 8)
    feats, start = session_profiles_fn(audio)
    clips, labels, order = [], [], []
    for i, e in enumerate(meta["events"]):
        if e.get("redo") or "end" not in e:
            continue
        s0 = max(0, e["start"] - int(PAD_S * FS))
        s1 = min(audio.shape[1] - FRAME, e["end"] + int(PAD_S * FS))
        if s1 - s0 < 4 * FRAME:
            continue
        clips.append(feats(s0, s1, mode).astype(np.float16))
        labels.append(e["command"])
        order.append(i)
    arr = np.empty(len(clips), dtype=object)  # ragged clips; np.array() would try to broadcast them
    arr[:] = clips
    np.savez_compressed(sdir / f"clips_{mode}.npz", clips=arr, labels=np.array(labels),
                        order=np.array(order), frame_start=start, mode=mode)
    return len(clips), audio.shape[1] / FS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--mode", default="enhanced", choices=["enhanced", "plain"])
    a = ap.parse_args()
    base = ROOT / "captures" / "echospeech"
    for ev in sorted(base.glob("*/*/events.json")):  # <person>/<session>/events.json
        sdir = ev.parent
        if sdir.parts[-2] == "_trash" or "_trash" in sdir.parts:
            continue
        if (sdir / f"clips_{a.mode}.npz").exists() and not a.force:
            continue
        n, secs = build(sdir, a.mode)
        print(f"{sdir.parent.name}/{sdir.name}: {n} clips from {secs:.0f} s")


if __name__ == "__main__":
    main()
