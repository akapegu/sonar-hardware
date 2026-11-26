#!/usr/bin/env python3
"""Compare silent-speech runs: speaking (7.5-15 s) vs still (3-6 s), 4-frame average, crop 226-306.
    .venv/bin/python tools/speech_contrast.py nf3_silent10 nf4_silent10
Writes captures/contrast_<last label>.png (mic 1 and mic 2 differential per run)."""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.ndimage import uniform_filter1d

labs = sys.argv[1:]
fig, ax = plt.subplots(len(labs), 2, figsize=(15, 3.5 * len(labs)), constrained_layout=True, squeeze=False)
for r, lab in enumerate(labs):
    P = np.load(f"captures/sonispeech_{lab}_profiles.npy")
    t = 1.2 + np.arange(P.shape[2] - 1) / 200
    for c in range(2):
        mag = np.abs(uniform_filter1d(P[0, c], 4, axis=0))
        d = np.abs(mag[1:] - mag[:-1])[:, 226:306].sum(axis=1)
        s, m = (t >= 3) & (t < 6), (t >= 7.5) & (t < 15)
        print(f"{lab} mic {c + 1}: speaking vs still {20 * np.log10(d[m].mean() / d[s].mean()):+.1f} dB")
        D = (mag[1:] - mag[:-1])[:, 180:360].T
        v = np.percentile(np.abs(D), 99.5)
        ax[r, c].imshow(D, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v,
                        extent=[1.2, 1.2 + D.shape[1] / 200, 180, 360])
        for x in (6.2, 15.5):
            ax[r, c].axvline(x, color="k", lw=.6, ls="--")
        ax[r, c].set(title=f"{lab} · mic {c + 1} · average 4", xlabel="s", ylabel="bin")
fig.savefig(f"captures/contrast_{labs[-1]}.png", dpi=60)
