#!/usr/bin/env python3
"""Experiment 1: capture raw PDM bits from mic_capture.ino and check both mics work.

Usage:
    python tools/mic_capture.py                 # auto-find the Teensy, record 5 s
    python tools/mic_capture.py -s 10 -p /dev/cu.usbmodem204786401

Writes captures/mic_<time>.npz (raw words) and .wav (stereo, 100 kHz; left = mic 1,
right = mic 2), prints health checks per mic, and shows spectra and spectrograms (0-50 kHz).
"""
import argparse
import glob
import struct
import sys
import time
from pathlib import Path

import numpy as np

HDR = struct.Struct("<4sIIIIIHHB3x")  # 32 bytes, matches mic_capture.ino
MAGIC = b"SNR1"


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("No /dev/cu.usbmodem* port. Is the Teensy plugged in and running mic_capture?")
    return ports[0]


def capture(port, seconds):
    import serial

    chunks, stats = [], []
    with serial.Serial(port, timeout=1) as ser:
        ser.reset_input_buffer()
        buf = bytearray()
        t_end = time.time() + seconds
        while time.time() < t_end:
            buf += ser.read(ser.in_waiting or 1)
            while True:
                i = buf.find(MAGIC)
                if i < 0:
                    del buf[:-3]
                    break
                if len(buf) - i < HDR.size:
                    del buf[:i]
                    break
                _, seq, us, tw, dw, fe, n, khz, nm = HDR.unpack_from(buf, i)
                end = i + HDR.size + 2 * n
                if len(buf) < end:
                    del buf[:i]
                    break
                chunks.append(np.frombuffer(bytes(buf[i + HDR.size:end]), "<u2"))
                stats.append((seq, us, tw, dw, fe, khz, nm))
                del buf[:end]
    if not stats:
        sys.exit("No packets received. Check that mic_capture.ino is uploaded.")
    return np.concatenate(chunks), np.array(stats, dtype=np.int64)


def pdm_to_audio(words):
    """Raw 16-bit words (MSB first) at 3.2 Mbit/s -> audio at 100 kHz."""
    from scipy.signal import resample_poly

    bits = np.unpackbits(words.astype(">u2").view(np.uint8))
    x = bits.astype(np.float32) * 2 - 1
    x = resample_poly(x, 1, 8)   # 3.2 MHz -> 400 kHz
    x = resample_poly(x, 1, 4)   # 400 kHz -> 100 kHz
    return x - x.mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port")
    ap.add_argument("-s", "--seconds", type=float, default=5)
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()

    port = a.port or find_port()
    print(f"Recording {a.seconds:g} s from {port} ...")
    words, st = capture(port, a.seconds)

    seq, us, tw, dw, fe, khz, nm = st.T
    n_mics = int(nm[-1])
    gaps = int(np.sum(np.diff(seq) != 1))
    dt = ((us[-1] - us[0]) % 2**32) / 1e6
    rate = ((tw[-1] - tw[0]) % 2**32) / dt if dt > 0 else float("nan")

    print(f"packets {len(seq)}, missing packets {gaps}, dropped words {dw[-1]}, "
          f"FIFO errors {fe[-1]}")
    print(f"clock reported by Teensy: {khz[-1]} kHz")
    print(f"measured word rate per mic: {rate:,.0f} words/s  (expect 200,000 at 3.2 MHz)")
    ok = abs(rate - 200_000) < 2_000 and gaps == 0 and dw[-1] == 0 and khz[-1] == 3200

    fs = 100_000
    mics = [np.ascontiguousarray(words[m::n_mics]) for m in range(n_mics)]
    audio = []
    for m, w in enumerate(mics, 1):
        ones = float(np.unpackbits(w.view(np.uint8)).mean())
        stuck = float(np.mean((w == 0) | (w == 0xFFFF)))
        x = pdm_to_audio(w)
        rms = float(np.sqrt(np.mean(x**2)))
        audio.append(x)
        good = 0.3 < ones < 0.7 and stuck < 0.2
        ok &= good
        print(f"mic {m}: ones density {ones:.3f} (expect ~0.50), stuck words {100 * stuck:.1f}% "
              f"(expect ~0%), RMS {20 * np.log10(rms + 1e-12):.1f} dBFS  -> "
              f"{'ok' if good else 'NO DATA?'}")
    print("RESULT:", "PASS" if ok else "CHECK the numbers above")

    out = Path(__file__).resolve().parent.parent / "captures"
    out.mkdir(exist_ok=True)
    stem = out / time.strftime("mic_%Y%m%d_%H%M%S")
    np.savez_compressed(f"{stem}.npz", words=words, stats=st)
    from scipy.io import wavfile
    stereo = np.stack(audio, axis=1)
    wavfile.write(f"{stem}.wav", fs, (stereo / max(1e-9, np.abs(stereo).max())).astype(np.float32))
    print(f"saved {stem}.npz and {stem}.wav")

    if a.no_plot:
        return
    import matplotlib.pyplot as plt
    from scipy.signal import welch

    fig, axes = plt.subplots(2, n_mics, figsize=(6 * n_mics, 7), squeeze=False)
    for m, x in enumerate(audio):
        f, p = welch(x, fs, nperseg=4096)
        axes[0, m].semilogy(f / 1000, p)
        axes[0, m].set(xlabel="kHz", ylabel="power", title=f"Mic {m + 1} spectrum", xlim=(0, 50))
        axes[0, m].grid(True, which="both", alpha=0.3)
        axes[1, m].specgram(x, NFFT=1024, Fs=fs, noverlap=768, cmap="magma")
        axes[1, m].set(xlabel="s", ylabel="Hz", title=f"Mic {m + 1} spectrogram")
    fig.suptitle("Jingle keys near the mics: ultrasound shows as energy above 20 kHz")
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
