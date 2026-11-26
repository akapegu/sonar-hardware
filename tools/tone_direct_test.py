#!/usr/bin/env python3
"""Record tone_direct.ino (speaker driven straight from pins 14/15) and compare with the amp path.

    .venv/bin/python tools/tone_direct_test.py [-s 22] [--amp captures/<sweep>.response.npz]

Each packet carries the tone frequency (100 Hz units, 0 = silence). For every tone the script
measures the tone's power on both mics, subtracts nothing, and reports it relative to the 20 kHz
tone; then it prints the amp-path response at the same frequencies for comparison.
"""
import argparse
import glob
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_capture import capture, find_port, pdm_to_audio  # noqa: E402

FS = 100_000
SAMPLES_PER_PKT = 256  # 512 words per mic x 16 bits / 32


def tone_db(x, f):
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
    fr = np.fft.rfftfreq(n, 1 / FS)
    sel = (fr > f - 300) & (fr < f + 300)
    return 10 * np.log10(X[sel].sum() + 1e-20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--seconds", type=float, default=22)
    ap.add_argument("--amp", default=None)
    a = ap.parse_args()
    words, st = capture(find_port(), a.seconds)
    tone = st[:, 7] * 100
    print(f"packets {len(st)}, dropped words {st[-1, 3]}")
    mics = {"mic 1 (pin 6)": 1, "mic 2 (pin 8)": 0}
    audio = {k: pdm_to_audio(np.ascontiguousarray(words[m::2])) for k, m in mics.items()}
    freqs = sorted(set(int(t) for t in tone if t > 0))
    sil = np.repeat(tone == 0, SAMPLES_PER_PKT)
    res = {k: {} for k in mics}
    for f in freqs:
        on = np.repeat(tone == f, SAMPLES_PER_PKT)
        for k, x in audio.items():
            n = min(len(x), len(on))
            seg = x[:n][on[:n]]
            seg = seg[len(seg) // 6:]           # skip the switch-over
            noise = x[:n][sil[:n]][2000:]
            res[k][f] = (tone_db(seg[:65536], f), tone_db(noise[:len(seg[:65536])], f))
    amp = None
    files = [a.amp] if a.amp else sorted(glob.glob("captures/*.response.npz"))
    if files:
        amp = np.load(files[-1])
        print(f"amp-path reference: {files[-1]}")
    print("\n tone      " + "".join(f"{k:>34}" for k in mics))
    print("           " + "".join(f"{'direct  (SNR)   via amp':>34}" for k in mics))
    for f in freqs:
        row = ""
        for k in mics:
            d0 = res[k][20000][0]
            v, nz = res[k][f]
            via = ""
            if amp is not None:
                key = "mic1" if "mic 1" in k else "mic2"
                ff, H = amp["f"], amp[key]
                ref = H[(ff > 19700) & (ff < 20300)].mean()
                via = f"{H[(ff > f - 300) & (ff < f + 300)].mean() - ref:+7.1f}"
            row += f"{v - d0:+9.1f} dB ({v - nz:4.0f}) {via:>10} dB"
        print(f"{f / 1e3:5.0f} kHz {row}")
    print("\n'direct' = speaker alone (no amp), relative to its 20 kHz tone.")
    print("'via amp' = earlier sweep through the amp, relative to 20 kHz.")
    print("If direct stays much flatter than via amp above 30 kHz, the amp filter is the main loss.")


if __name__ == "__main__":
    main()
