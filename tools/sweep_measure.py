#!/usr/bin/env python3
"""Measure the speaker->mic response from a speaker_sweep.ino recording by synchronous averaging.

Playback and recording share one clock, so the 200 ms sweep period is exactly 20,000 samples
at 100 kHz. Averaging all periods coherently lowers the noise by 10*log10(N) dB. The response is
received-chirp spectrum / transmitted-chirp spectrum; the noise floor comes from the silent half
of the averaged period.

    .venv/bin/python tools/sweep_measure.py captures/mic_YYYYmmdd_HHMMSS.npz
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_capture import pdm_to_audio  # noqa: E402

FS, PERIOD, CHIRP, FADE = 100_000, 20_000, 10_000, 500
F0, F1 = 15_000.0, 45_000.0


def tx_chirp():
    n = np.arange(CHIRP)
    t = n / FS
    k = (F1 - F0) / (CHIRP / FS)
    v = np.sin(2 * np.pi * (F0 * t + 0.5 * k * t * t))
    w = np.ones(CHIRP)
    w[:FADE] = 0.5 - 0.5 * np.cos(np.pi * n[:FADE] / FADE)
    m = n >= CHIRP - FADE
    w[m] = 0.5 - 0.5 * np.cos(np.pi * (CHIRP - 1 - n[m]) / FADE)
    x = v * w
    x -= x.sum() / w.sum() * w
    return x / np.abs(x).max()


def measure(x):
    n = len(x) // PERIOD
    avg = x[: n * PERIOD].reshape(n, PERIOD).mean(axis=0)
    # find the chirp start: offset whose 10,000-sample circular window holds the most energy
    e = np.concatenate([avg, avg]) ** 2
    c = np.cumsum(e)
    win = c[CHIRP:CHIRP + PERIOD] - c[:PERIOD]
    off = int(np.argmax(win))
    seg = np.roll(avg, -off)
    L = 11_000                       # chirp + 10 ms of echoes
    rx = seg[:L]
    noise = seg[12_000:] * np.sqrt(L / (PERIOD - 12_000))  # silent part only, scaled to L
    nfft = 1 << 15
    f = np.fft.rfftfreq(nfft, 1 / FS)
    T = np.fft.rfft(tx_chirp(), nfft)
    R = np.fft.rfft(rx, nfft)
    N = np.fft.rfft(noise, nfft)
    H = 20 * np.log10(np.abs(R) / (np.abs(T) + 1e-12) + 1e-15)
    Nf = 20 * np.log10(np.abs(N) / (np.abs(T) + 1e-12) + 1e-15)
    return f, H, Nf, n, off


def main():
    d = np.load(sys.argv[1])
    w, lv = d["words"], d["stats"][:, 7]
    skip = int(1.5 * 400_000)        # drop the 1 s soft start
    print(f"level max {lv.max() / 10:.1f}%")
    res = {}
    for m, name in ((1, "mic 1 (pin 6)"), (0, "mic 2 (pin 8)")):
        x = pdm_to_audio(np.ascontiguousarray(w[skip:][m::2]))
        res[name] = measure(x)
    f = res[next(iter(res))][0]
    ref_sel = (f >= 18_000) & (f < 20_000)
    print(f"averaged {res[next(iter(res))][3]} chirps\n")
    print("band kHz     " + "".join(f"{k:>26}" for k in res))
    print("             " + "".join(f"{'re 18-20k   SNR':>26}" for k in res))
    bands = [(15, 18), (18, 20), (20, 22), (22, 24), (24, 26), (26, 28), (28, 30), (30, 32), (32, 34),
             (34, 36), (36, 38), (38, 40), (40, 42), (42, 45)]
    for lo, hi in bands:
        sel = (f >= lo * 1e3) & (f < hi * 1e3)
        row = ""
        for k, (f_, H, Nf, n, off) in res.items():
            ref = H[ref_sel].mean()
            row += f"{H[sel].mean() - ref:+14.1f} dB {H[sel].mean() - Nf[sel].mean():6.1f} dB"
        print(f"{lo:>4}-{hi:<4}    {row}")
    print("\nSoniSpeech bands (mean, re 18-20 kHz):")
    for k, (f_, H, Nf, n, off) in res.items():
        ref = H[ref_sel].mean()
        b0 = H[(f >= 18e3) & (f <= 28e3)].mean() - ref
        b1 = H[(f >= 29e3) & (f <= 39e3)].mean() - ref
        print(f"  {k}: 18-28 kHz {b0:+.1f} dB, 29-39 kHz {b1:+.1f} dB  -> difference {b0 - b1:.1f} dB")
    out = Path(sys.argv[1]).with_suffix(".response.npz")
    np.savez_compressed(out, f=f, **{k.split()[0] + k.split()[1]: v[1] for k, v in res.items()},
                        **{"noise_" + k.split()[0] + k.split()[1]: v[2] for k, v in res.items()})
    print(f"saved {out}")


if __name__ == "__main__":
    main()
