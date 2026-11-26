#!/usr/bin/env python3
"""Take-turns recording: both speakers play the official 18-28 kHz chirp on alternate 5 ms frames.

Needs experiments/take_turns/take_turns.ino on the Teensy (the official sonispeech_mode has no
take-turns mode). Reuses the shared helpers in tools/ without changing them.

    .venv/bin/python experiments/take_turns/tdm_capture.py --label tt_test --test
        --test: spoken cues (still -> "move" at ~5 s -> 10 s of silent speech -> "stop"), then prints
        the enhanced speech contrast per path and opens the 4-path picture.
    .venv/bin/python experiments/take_turns/tdm_capture.py --label tt_rec -s 30

Output (captures/): tt_<label>.wav (dataset format, 100 kHz 2-ch int16), tt_<label>_profiles.npy
(2 speakers x 2 mics x frames x 500 at 200 fps; each speaker's 100 fps frames repeated twice) and
tt_<label>_paths.png.
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
from mic_capture import HDR, MAGIC, find_port, pdm_to_audio  # noqa: E402
from sonispeech_capture import echo_profiles, enhanced_diff, read_tx  # noqa: E402

FS = 100_000
LEAD_FRAMES_IN_WAV = 140  # firmware lead is 400 frames (2 s); the WAV starts 1.2 s in


def record(seconds, level, taper):
    import serial
    chunks, buf = [], bytearray()
    lv = int(round(level * 10))
    with serial.Serial(find_port(), timeout=0.2) as ser:
        ser.write(bytes([ord("C"), 1, 1, 0, ord("W"), int(taper), 0, 0,
                         ord("L"), lv & 255, lv >> 8, 0, ord("T"), 1, 0, 0]))
        ser.reset_input_buffer()
        t_end = time.time() + seconds
        flags = None
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
    if flags is None or not flags & 8:
        sys.exit(f"take-turns flag not set (flags={flags}); is take_turns.ino on the Teensy?")
    return np.concatenate(chunks)


def split_tdm(prof0, lead_frames):
    """prof0: (2 mics, frames, 500) from the 18-28 kHz chirp. During the lead only speaker 0
    plays, so its frames have the stronger direct path. Returns (2 spk, 2 mic, frames, 500)."""
    lead = np.abs(prof0[:, :lead_frames]).max(axis=-1).mean(axis=0)
    p0 = 0 if lead[0::2].mean() > lead[1::2].mean() else 1
    n = (prof0.shape[1] - p0) // 2 * 2
    s0, s1 = prof0[:, p0:p0 + n:2], prof0[:, p0 + 1:p0 + n:2]
    return np.stack([np.repeat(s0, 2, axis=1), np.repeat(s1, 2, axis=1)]), p0


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
    words = record(a.seconds, a.level, a.taper)
    skip = int(1.2 * 400_000)
    m1 = pdm_to_audio(np.ascontiguousarray(words[skip:][1::2]))
    m2 = pdm_to_audio(np.ascontiguousarray(words[skip:][0::2]))
    n = min(len(m1), len(m2))
    audio = np.stack([m1[:n], m2[:n]], axis=1)
    out = ROOT / "captures"
    out.mkdir(exist_ok=True)
    with wave.open(str(out / f"tt_{a.label}.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS)
        w.writeframes(np.clip(audio * 32767, -32768, 32767).astype("<i2").tobytes())

    p0, start = echo_profiles(audio * 32767 / 32768, [read_tx()[0]])
    prof, parity = split_tdm(p0[0], LEAD_FRAMES_IN_WAV)
    np.save(out / f"tt_{a.label}_profiles.npy", prof.astype(np.float32))
    print(f"speaker 0 on frame parity {parity}; frames {prof.shape[2]}")
    for s in range(2):
        for c in range(2):
            med = np.median(np.abs(prof[s, c, 2 * LEAD_FRAMES_IN_WAV:]), axis=0)
            print(f"  speaker {s} -> mic {c + 1}: direct path bin {int(np.argmax(med))}, "
                  f"peak/median {20 * np.log10(med.max() / np.median(med)):5.1f} dB")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    E = enhanced_diff(prof)
    t = 1.2 + np.arange(prof.shape[2]) / 200
    fig, ax = plt.subplots(4, 1, figsize=(12, 10), constrained_layout=True)
    row = []
    for r, (s, c) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
        en = np.abs(E[s, c])[:, 226:306].sum(1)
        if a.test:
            row.append(20 * np.log10(en[(t >= 6.5) & (t < 15)].mean() / en[(t >= 3) & (t < 5)].mean()))
        z = E[s, c][1:, 180:360].T
        v = np.percentile(np.abs(z), 99.5)
        ax[r].imshow(z, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v,
                     extent=[1.2, 1.2 + z.shape[1] / 200, 180, 360])
        for y in (226, 306):
            ax[r].axhline(y, color="g", lw=.5)
        if a.test:
            for x in (5.6, 15.2):
                ax[r].axvline(x, color="k", lw=.8, ls="--")
        ax[r].set(title=f"take turns · speaker {s} (18-28 kHz) -> mic {c + 1} · enhanced", xlabel="s", ylabel="bin")
    png = out / f"tt_{a.label}_paths.png"
    fig.savefig(png, dpi=60)
    if a.test:
        print("enhanced speech contrast  " + "  ".join(f"s{s}m{c + 1} {v:+5.1f}" for (s, c), v in
                                                     zip([(0, 0), (0, 1), (1, 0), (1, 1)], row)) + " dB")
        subprocess.run(["open", str(png)])
    print(f"saved {png.name}")


if __name__ == "__main__":
    main()
