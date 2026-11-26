#!/usr/bin/env python3
"""Try each BCLK drive strength (pin 21) and report the noise on both mics.

Needs speaker_sweep.ino. Keep the speaker DISCONNECTED and the room quiet.
    .venv/bin/python tools/pad_scan.py
"""
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_capture import HDR, MAGIC, find_port, pdm_to_audio  # noqa: E402


def grab(ser, seconds):
    out, buf, pads = [], bytearray(), set()
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
            out.append(np.frombuffer(bytes(buf[i + HDR.size:end]), "<u2"))
            pads.add(f[9])
            del buf[:end]
    return np.concatenate(out), pads


def noise_db(w):
    from scipy.signal import butter, sosfilt
    sos = butter(6, [5e3, 45e3], "bandpass", fs=100_000, output="sos")
    a = sosfilt(sos, pdm_to_audio(np.ascontiguousarray(w)))[5000:]
    return 20 * np.log10(np.std(a) + 1e-12)


def main():
    import serial
    # flags: bit0 hysteresis, bit1 fast slew, bit2 keeper off, bit3 sample on falling edge
    configs = [(d, sp, fl) for fl in (1, 3, 9, 11, 0, 8) for sp in (2,) for d in range(1, 8)]
    with serial.Serial(find_port(), timeout=0.2) as ser:
        print("DSE SPD FLAGS(edge,slew,hys) |  pin8 (mic2)  pin6 (mic1)  [dB; clean ~ -80]")
        for d, sp, fl in configs:
            ser.write(bytes([ord("P"), d, sp, fl]))
            time.sleep(0.2)
            ser.reset_input_buffer()
            grab(ser, 0.3)  # flush
            w, pads = grab(ser, 1.0)
            n8, n6 = noise_db(w[0::2]), noise_db(w[1::2])
            flag = "  <- both clean" if max(n8, n6) < -70 else ""
            desc = f"{'fall' if fl & 8 else 'rise'},{'fast' if fl & 2 else 'slow'},{'hys' if fl & 1 else '---'}"
            print(f" {d}   {sp}   {desc:16} |  {n8:8.1f}    {n6:8.1f}{flag}")
        ser.write(bytes([ord("P"), 6, 2, 1]))


if __name__ == "__main__":
    main()
