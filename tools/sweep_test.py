#!/usr/bin/env python3
"""Experiment 2: record the 15-45 kHz sweep from speaker_sweep.ino and measure the response.

The Teensy only plays while this script has the port open (soft start 1 s, fade out 0.1 s).

    .venv/bin/python tools/sweep_test.py --label nospeaker      # dry run, no speaker connected
    .venv/bin/python tools/sweep_test.py --label speaker --baseline captures/sweep_nospeaker.npz

For each mic it takes the 95th-percentile power of every frequency bin over the recording
(the chirp passes through each bin 5 times a second), then reports, per 2.5 kHz band, how far
that is above the baseline (no-speaker) recording.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_capture import capture, find_port, pdm_to_audio  # noqa: E402

FS = 100_000
NPERSEG = 1024  # ~98 Hz bins, 10 ms frames


def response(x):
    from scipy.signal import stft

    f, _, z = stft(x, FS, nperseg=NPERSEG, noverlap=NPERSEG // 2)
    p = np.abs(z) ** 2
    return f, 10 * np.log10(np.percentile(p, 95, axis=1) + 1e-20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port")
    ap.add_argument("-s", "--seconds", type=float, default=6)
    ap.add_argument("--label", required=True)
    ap.add_argument("--baseline", help="npz from an earlier run (e.g. the no-speaker dry run)")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()

    port = a.port or find_port()
    print(f"Recording {a.seconds:g} s from {port} (sound plays only while recording) ...")
    words, st = capture(port, a.seconds)
    seq, us, tw, dw, fe, khz, nm, lv = st.T
    print(f"packets {len(seq)}, missing {int(np.sum(np.diff(seq) != 1))}, dropped words {dw[-1]}, "
          f"clock {khz[-1]} kHz")
    print(f"output level: started {lv[0] / 10:.1f}%, max {lv.max() / 10:.1f}%, "
          f"end {lv[-1] / 10:.1f}%  (expect ramp to 10.0%, never above 14%)")
    if lv.max() > 140:
        print("WARNING: level above the 14% cap. Unplug the USB.")

    # Skip the first 1.5 s (soft start) so the response is measured at full level.
    skip = int(1.5 * 200_000) * 2
    words = words[skip:] if len(words) > skip * 2 else words
    n_mics = int(nm[-1])
    resp = {}
    for m in range(n_mics):
        x = pdm_to_audio(np.ascontiguousarray(words[m::n_mics]))
        f, r = response(x)
        resp[f"pin{8 if m == 0 else 6}"] = r

    out = Path(__file__).resolve().parent.parent / "captures"
    out.mkdir(exist_ok=True)
    path = out / f"sweep_{a.label}.npz"
    np.savez_compressed(path, f=f, **resp, level=lv, stamp=time.strftime("%Y%m%d_%H%M%S"))
    print(f"saved {path}")

    base = np.load(a.baseline) if a.baseline else None
    edges = np.arange(10_000, 50_001, 2_500)
    print("\nband (kHz)   " + "   ".join(f"{k:>14}" for k in resp))
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (f >= lo) & (f < hi)
        cells = []
        for k, r in resp.items():
            v = r[sel].mean()
            if base is not None:
                cells.append(f"{v:6.1f} ({v - base[k][sel].mean():+5.1f})")
            else:
                cells.append(f"{v:14.1f}")
        print(f"{lo / 1e3:4.1f}-{hi / 1e3:4.1f}    " + "   ".join(cells))
    if base is not None:
        print("(numbers in brackets = dB above the baseline; > +10 dB means the speaker is clearly heard)")

    if a.no_plot:
        return
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    for k, r in resp.items():
        ax.plot(f / 1e3, r, label=f"{k} ({a.label})")
        if base is not None:
            ax.plot(f / 1e3, base[k], "--", alpha=0.6, label=f"{k} (baseline)")
    for x, txt in ((24, "amp flat to 24k"), (31, "amp -3 dB 31k")):
        ax.axvline(x, color="gray", ls=":", lw=1)
        ax.text(x, ax.get_ylim()[1], txt, rotation=90, va="top", fontsize=8)
    ax.set(xlim=(10, 50), xlabel="kHz", ylabel="dB (95th percentile)", title="Sweep response")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
