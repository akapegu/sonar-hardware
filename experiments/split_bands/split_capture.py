#!/usr/bin/env python3
"""Split-bands recording: speaker 0 plays 18-24 kHz, speaker 1 plays 25-31 kHz, both at once.

Needs experiments/split_bands/split_bands.ino on the Teensy. Reuses tools/ helpers unchanged
(echo profiles as the official code, with this experiment's chirps and band-pass ranges).

    .venv/bin/python experiments/split_bands/split_capture.py --label sb_test --test
"""
import argparse
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import sonispeech_capture as sc  # noqa: E402
from mic_capture import HDR, MAGIC, find_port, pdm_to_audio  # noqa: E402

FS, N, TAPER = 100_000, 500, 20
BANDS = [(18000.0, 24000.0), (25000.0, 31000.0)]
BP = [[17500, 24500], [24500, 31500]]


def chirp(f0, f1, taper=True):
    n = np.arange(N)
    t, T = n / FS, N / FS
    x = np.sin(2 * np.pi * (f0 * t + 0.5 * (f1 - f0) / T * t * t))
    if taper:
        w = np.ones(N)
        w[:TAPER] = 0.5 - 0.5 * np.cos(np.pi * n[:TAPER] / TAPER)
        w[-TAPER:] = 0.5 - 0.5 * np.cos(np.pi * (N - 1 - n[-TAPER:]) / TAPER)
        x = x * w
    return x


def record(seconds, level, taper):
    import serial
    chunks, buf, flags = [], bytearray(), None
    lv = int(round(level * 10))
    with serial.Serial(find_port(), timeout=0.2) as ser:
        ser.write(bytes([ord("C"), 1, 1, 0, ord("W"), int(taper), 0, 0, ord("L"), lv & 255, lv >> 8, 0]))
        ser.reset_input_buffer()
        t_end = time.time() + seconds
        while time.time() < t_end:
            buf += ser.read(ser.in_waiting or 1)
            while True:
                i = buf.find(MAGIC)
                if i < 0 or len(buf) - i < HDR.size:
                    break
                f = HDR.unpack_from(buf, i)
                end = i + HDR.size + 2 * f[6]
                if len(buf) < end:
                    break
                chunks.append(np.frombuffer(bytes(buf[i + HDR.size:end]), "<u2"))
                flags = f[9]
                del buf[:end]
    return np.concatenate(chunks), flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("-s", "--seconds", type=float, default=20)
    ap.add_argument("--level", type=float, default=10.0)
    ap.add_argument("--no-taper", dest="taper", action="store_false")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        subprocess.Popen([sys.executable, "-c", "import time,subprocess;time.sleep(0.5);subprocess.run(['say','get ready']);"
                          "time.sleep(4.5);subprocess.run(['say','move']);time.sleep(9.5);subprocess.run(['say','stop'])"])
        a.seconds = 20
    words, flags = record(a.seconds, a.level, a.taper)
    skip = int(1.2 * 400_000)
    m1 = pdm_to_audio(np.ascontiguousarray(words[skip:][1::2]))
    m2 = pdm_to_audio(np.ascontiguousarray(words[skip:][0::2]))
    n = min(len(m1), len(m2))
    audio = np.stack([m1[:n], m2[:n]], axis=1)
    out = ROOT / "captures"
    with wave.open(str(out / f"sb_{a.label}.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS)
        w.writeframes(np.clip(audio * 32767, -32768, 32767).astype("<i2").tobytes())

    sc.BP = BP
    tx = [chirp(*BANDS[0], a.taper), chirp(*BANDS[1], a.taper)]
    prof, start = sc.echo_profiles(audio * 32767 / 32768, tx)
    np.save(out / f"sb_{a.label}_profiles.npy", prof.astype(np.float32))
    print(f"flags {flags:03b}")
    for s in range(2):
        for c in range(2):
            med = np.median(np.abs(prof[s, c]), axis=0)
            print(f"  speaker {s} ({BANDS[s][0] / 1e3:.0f}-{BANDS[s][1] / 1e3:.0f} kHz) -> mic {c + 1}: "
                  f"direct path bin {int(np.argmax(med))}, peak/median {20 * np.log10(med.max() / np.median(med)):5.1f} dB")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    E = sc.enhanced_diff(prof)
    t = 1.2 + np.arange(prof.shape[2]) / 200
    fig, ax = plt.subplots(4, 1, figsize=(12, 10), constrained_layout=True)
    row = []
    for r, (s, c) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
        en = np.abs(E[s, c])[:, 226:306].sum(1)
        if a.test:
            row.append(20 * np.log10(en[(t >= 6.5) & (t < 15)].mean() / en[(t >= 2.5) & (t < 5)].mean()))
        z = E[s, c][1:, 180:360].T
        v = np.percentile(np.abs(z), 99.5)
        ax[r].imshow(z, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v,
                     extent=[1.2, 1.2 + z.shape[1] / 200, 180, 360])
        for y in (226, 306):
            ax[r].axhline(y, color="g", lw=.5)
        if a.test:
            for x in (5.6, 15.2):
                ax[r].axvline(x, color="k", lw=.8, ls="--")
        ax[r].set(title=f"split bands · speaker {s} ({BANDS[s][0] / 1e3:.0f}-{BANDS[s][1] / 1e3:.0f} kHz) -> mic {c + 1} · enhanced",
                  xlabel="s", ylabel="bin")
    png = out / f"sb_{a.label}_paths.png"
    fig.savefig(png, dpi=60)
    if a.test:
        print("enhanced speech contrast  " + "  ".join(f"s{s}m{c + 1} {v:+5.1f}" for (s, c), v in
                                                     zip([(0, 0), (0, 1), (1, 0), (1, 1)], row)) + " dB")
        subprocess.run(["open", str(png)])


if __name__ == "__main__":
    main()
