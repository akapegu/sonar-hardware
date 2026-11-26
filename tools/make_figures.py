#!/usr/bin/env python3
"""Regenerate the README figures in docs/img/ from local recordings in captures/.

    .venv/bin/python tools/make_figures.py
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
from sonispeech_capture import enhanced_diff  # noqa: E402

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


INK, INK2, GRID = "#1f2328", "#59636e", "#e6e8eb"


def signal_plans():
    """Back-to-back enhanced speech contrast per path (values from experiments/*/README.md)."""
    paths = ["spk 0 → mic 1", "spk 0 → mic 2", "spk 1 → mic 1", "spk 1 → mic 2"]
    modes = [("Official 18–28 / 29–39 kHz", [9.9, 10.0, -0.3, 0.0], "#257de0"),
             ("Take turns, both 18–28 kHz", [6.0, 5.4, 2.1, 1.5], "#f47230"),
             ("Split 18–24 / 25–31 kHz", [11.2, 10.0, 8.2, 6.9], "#00b894")]
    fig, ax = plt.subplots(figsize=(9, 3.8), constrained_layout=True)
    x, w = np.arange(4), 0.26
    for i, (name, vals, col) in enumerate(modes):
        xs = x + (i - 1) * (w + 0.01)
        ax.bar(xs, vals, w, color=col, label=name, edgecolor="white", linewidth=2)
        for xx, v in zip(xs, vals):
            ax.text(xx, v + (0.3 if v >= 0 else -0.9), f"{v:+.1f}", ha="center", fontsize=8, color=INK)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(paths, color=INK)
    ax.set_ylabel("speech contrast, dB\n(speaking vs still)", color=INK2)
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.set_ylim(-2, 13.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc="upper right", labelcolor=INK)
    ax.set_title("Signal plans compared back to back on the glasses (10 s silent counting, enhanced processing)",
                 color=INK, fontsize=10.5, loc="left")
    fig.savefig(IMG / "signal_plans.png", dpi=90, facecolor="white")


def split_bands_paths():
    prof = np.load(CAP / "sb_sb_quiet1_profiles.npy")
    E = enhanced_diff(prof)
    names = ["speaker 0 (18–24 kHz) → mic 1", "speaker 0 (18–24 kHz) → mic 2",
             "speaker 1 (25–31 kHz) → mic 1", "speaker 1 (25–31 kHz) → mic 2"]
    vals = [14.8, 10.9, 9.2, 6.0]
    fig, ax = plt.subplots(4, 1, figsize=(10, 8.6), constrained_layout=True, sharex=True)
    for r, ((s, c), nm, v) in enumerate(zip([(0, 0), (0, 1), (1, 0), (1, 1)], names, vals)):
        z = E[s, c][1:, 180:360].T
        m = np.percentile(np.abs(z), 99.5)
        ax[r].imshow(z, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-m, vmax=m,
                     extent=[1.2, 1.2 + z.shape[1] / 200, 180, 360])
        for y in (226, 306):
            ax[r].axhline(y, color="#2da44e", lw=.7)
        for xx in (7.5, 18.5):
            ax[r].axvline(xx, color=INK, lw=.8, ls="--")
        ax[r].set_title(f"{nm}  ·  contrast {v:+.1f} dB", loc="left", fontsize=10, color=INK)
        ax[r].set_ylabel("range bin", color=INK2)
    ax[-1].set_xlabel("s  (dashed: silent counting 1–10, detected; green: model crop 226–306)", color=INK2)
    fig.suptitle("Silent speech on the glasses: split bands, both speakers, both mics (enhanced echo profiles)",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.savefig(IMG / "split_bands_4paths.png", dpi=72, facecolor="white")


def split_bands_zoom(t0=9.0, t1=13.5):
    """4.5 s of the silent counting, model crop only, each path standardised (±3 sigma)."""
    prof = np.load(CAP / "sb_sb_quiet1_profiles.npy")
    E = enhanced_diff(prof)
    t = 1.2 + np.arange(prof.shape[2]) / 200
    sel = (t >= t0) & (t < t1)
    names = ["s0m1", "s0m2", "s1m1", "s1m2"]
    fig, ax = plt.subplots(4, 1, figsize=(11, 7.2), constrained_layout=True, sharex=True)
    for r, (s, c) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
        full = E[s, c][:, 226:306]
        z = (full - full.mean()) / full.std()
        ax[r].imshow(z[sel].T, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-3, vmax=3,
                     extent=[0, t1 - t0, 226, 306], interpolation="nearest")
        ax[r].set_yticks([226, 306])
        ax[r].set_ylabel(names[r], rotation=0, labelpad=22, color=INK2, va="center")
        for sp in ax[r].spines.values():
            sp.set_visible(False)
        ax[r].tick_params(colors=INK2)
    ax[-1].set_xlabel(f"seconds  ·  {t0:.1f}–{t1:.1f} s of the recording  ·  4 paths × 80 range bins  ·  "
                      "colour = standardised value, ±3σ", color=INK2)
    fig.suptitle("Silent counting (split bands 18–24 / 25–31 kHz, enhanced echo profiles)",
                 x=0.02, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.savefig(IMG / "split_bands_zoom.png", dpi=110, facecolor="white")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    a = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    response()
    signal_plans()
    split_bands_paths()
    split_bands_zoom()
    for p in sorted(IMG.glob("*.png")):
        print(p.relative_to(ROOT), f"{p.stat().st_size // 1024} KB")
