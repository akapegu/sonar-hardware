#!/usr/bin/env python3
"""Regenerate the README figures in docs/img/ from local recordings in captures/.

    .venv/bin/python tools/make_figures.py [--dataset <SoniSpeech silent session audio.wav>]
"""
import argparse
import sys
import wave
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sonispeech_capture import echo_profiles, enhanced_diff, read_tx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAP, IMG = ROOT / "captures", ROOT / "docs" / "img"
plt.rcParams.update({"font.size": 10})


def response():
    r = np.load(CAP / "mic_20261126_131000.response.npz")
    f = r["f"] / 1e3
    sel = (f >= 15) & (f <= 45)
    fig, ax = plt.subplots(figsize=(9, 3.6), constrained_layout=True)
    for key, lab in (("mic1", "mic 1"), ("mic2", "mic 2")):
        h = r[key]
        ref = h[(f >= 18) & (f < 20)].mean()
        k = 41
        sm = np.convolve(h - ref, np.ones(k) / k, mode="same")
        ax.plot(f[sel], sm[sel], label=lab)
    ax.axvspan(18, 28, color="tab:green", alpha=.12, label="SoniSpeech band 0 (18-28 kHz)")
    ax.axvspan(29, 39, color="tab:red", alpha=.12, label="SoniSpeech band 1 (29-39 kHz)")
    ax.set(xlabel="kHz", ylabel="dB re 18-20 kHz", ylim=(-55, 10),
           title="Speaker -> mic response (OWR-0640T-16H via MAX98357A, 92-sweep average)")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, loc="lower left")
    fig.savefig(IMG / "response_15_45k.png", dpi=90)


def load_wav(p, t0=0.0, dur=None):
    with wave.open(str(p)) as w:
        fs = w.getframerate()
        w.setpos(int(t0 * fs))
        n = w.getnframes() - int(t0 * fs) if dur is None else int(dur * fs)
        return np.frombuffer(w.readframes(n), "<i2").reshape(-1, 2) / 32768


def glasses(dataset):
    prof = np.load(CAP / "sonispeech_nf6_silent20s_profiles.npy")
    rows = [("Official differential profile (what the model reads)", np.diff(np.abs(prof[0, 0]), axis=0)),
            ("Enhanced: 4-frame average, 50 ms difference", enhanced_diff(prof[0, 0])[1:])]
    if dataset:
        ds = load_wav(dataset, 60, 25)
        pd, _ = echo_profiles(ds * 32767 / 32768, read_tx())
        rows.insert(0, ("SoniSpeech dataset, silent session (official differential)", np.diff(np.abs(pd[0, 0]), axis=0)))
    fig, ax = plt.subplots(len(rows), 1, figsize=(10, 2.6 * len(rows)), constrained_layout=True)
    for a, (title, d) in zip(ax, rows):
        z = d[:, 180:360].T
        v = np.percentile(np.abs(z), 99.5)
        x0 = 0.0 if "dataset" in title else 1.2
        a.imshow(z, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v,
                 extent=[x0, x0 + z.shape[1] / 200, 180, 360])
        a.axhline(226, color="g", lw=.6)
        a.axhline(306, color="g", lw=.6)
        if "dataset" not in title:
            for x in (6.2, 25.8):
                a.axvline(x, color="k", lw=.8, ls="--")
        a.set(title=title, ylabel="range bin")
    ax[-1].set_xlabel("s (ours: dashed = start / stop of silent counting; green = model crop 226-306)")
    fig.savefig(IMG / "silent_speech_glasses.png", dpi=80)


def motion():
    prof = np.load(CAP / "sonispeech_motion_02b_profiles.npy")
    z = np.diff(np.abs(prof[0, 1]), axis=0)[:, 200:450].T
    v = np.percentile(np.abs(z), 99.5)
    fig, ax = plt.subplots(figsize=(10, 2.8), constrained_layout=True)
    ax.imshow(z, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v, extent=[1.2, 1.2 + z.shape[1] / 200, 200, 450])
    for x in (7.6, 13.2):
        ax.axvline(x, color="k", lw=.8, ls="--")
    ax.set(title="Bench: hand moving 10-20 cm above the rig at 2% level (inaudible), 18-28 kHz, mic 2",
           xlabel="s (dashed: move / stop)", ylabel="range bin")
    fig.savefig(IMG / "hand_motion_2pct.png", dpi=80)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=None)
    a = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    response()
    glasses(a.dataset)
    motion()
    for p in sorted(IMG.glob("*.png")):
        print(p.relative_to(ROOT), f"{p.stat().st_size // 1024} KB")
