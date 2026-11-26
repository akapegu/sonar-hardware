#!/usr/bin/env python3
"""Record in SoniSpeech mode and compute echo profiles the way the dataset's official code does.

    .venv/bin/python tools/sonispeech_capture.py --label test -s 10 --channels A [--no-taper]
    --channels A | B | AB | none   which amps play (none = silent reference)
    --level 5      playback level in percent (only lower than 10 has an effect)

Writes captures/sonispeech_<label>.wav (100 kHz, 2 ch int16: ch0 = mic 1/pin 6, ch1 = mic 2/pin 8,
the dataset's format), captures/sonispeech_<label>_profiles.npy (2 tx x 2 mics x frames x 500),
and a PNG of |profile| and the differential profile. Prints the audible-band level (< 15 kHz).

The echo-profile steps are re-implemented from echo_profiles.py in the dataset (5th-order
Butterworth band-pass 17.5-28.5 / 28.5-39.5 kHz, start position from the averaged correlation,
then per-frame np.correlate with the official chirp). Chirps are read from the dataset folder.
"""
import argparse
import sys
import time
import wave
from array import array
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mic_capture import HDR, MAGIC, find_port, pdm_to_audio  # noqa: E402

FS, FRAME = 100_000, 500
TX_DIR = Path("~/Downloads/sonispeech_datasets/data/SoniSpeech_01_of_09/echo_profiles/tx_signals").expanduser()
TX_FILES = ["fmcw18000_b10000_l500_100k.wav", "fmcw29000_b10000_l500_100k.wav"]
BP = [[17500, 28500], [28500, 39500]]
ROOT = Path(__file__).resolve().parent.parent


def read_tx():
    out = []
    for f in TX_FILES:
        with wave.open(str(TX_DIR / f)) as w:
            out.append(np.array(array("h", w.readframes(w.getnframes())), float) / 32768.0)
    return out


def record(seconds, chan, taper, level):
    import serial
    ena, enb = {"A": (1, 0), "B": (0, 1), "AB": (1, 1), "none": (0, 0)}[chan]
    chunks, stats, buf = [], [], bytearray()
    with serial.Serial(find_port(), timeout=0.2) as ser:
        lv = int(round(level * 10))
        ser.write(bytes([ord("C"), ena, enb, 0, ord("W"), int(taper), 0, 0, ord("L"), lv & 255, lv >> 8, 0]))
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
                stats.append((f[1], f[4], f[9], f[10]))  # seq, dropped, flags, level A
                del buf[:end]
    return np.concatenate(chunks), np.array(stats)


def echo_profiles(audio, tx_signals, align=None):
    """align: which tx bands to use for start-position detection (default all, as the official code).
    With only one amp playing, aligning on that band alone stops the other band's leakage peak
    from winning."""
    from scipy.signal import butter, lfilter
    n_tx, n_ch = len(tx_signals), audio.shape[1]
    filt = np.zeros((n_tx, n_ch, audio.shape[0]))
    for n in range(n_tx):
        b, a = butter(5, [BP[n][0] / (FS / 2), BP[n][1] / (FS / 2)], btype="band")
        for c in range(n_ch):
            filt[n, c] = lfilter(b, a, audio[:, c])
    L = min(FRAME * 100000, audio.shape[0] // (FRAME * n_tx * n_ch) * (FRAME * n_tx * n_ch))
    align = list(range(n_tx)) if align is None else list(align)
    sp = np.zeros((len(align), n_ch, L))
    for k, n in enumerate(align):
        for c in range(n_ch):
            sp[k, c] = np.correlate(filt[n, c][:L], tx_signals[n], mode="full")[FRAME - 1:]
    # median over frames (the official code uses the mean): same result on steady recordings, but a
    # short large movement can no longer pull the alignment away from the direct path
    sp = np.median(np.abs(sp.reshape(-1, FRAME)), axis=0)
    start = (int(np.argmax(sp)) + FRAME - FRAME // 2) % FRAME
    filt = filt[:, :, start:]
    filt = filt[:, :, :filt.shape[2] - filt.shape[2] % FRAME].reshape(n_tx, n_ch, -1, FRAME)
    prof = np.zeros((n_tx, n_ch, filt.shape[2], FRAME))
    for n, tx in enumerate(tx_signals):
        T = np.fft.rfft(tx[::-1], 2 * FRAME)
        X = np.fft.rfft(filt[n], 2 * FRAME, axis=-1)
        full = np.fft.irfft(X * T, 2 * FRAME, axis=-1)[..., :2 * FRAME - 1]  # == np.correlate 'full'
        prof[n] = full[..., FRAME - 1:2 * FRAME - 1]
    return prof, start


def enhanced_diff(prof, avg=4, lag=10):
    """Movement picture with more contrast than the official frame difference.

    The official feature is |p_t| - |p_(t-1)|: frames 5 ms apart, so slow mouth movements barely
    change between them while the noise does. Here the profile is first averaged over `avg`
    frames (playback and recording share one clock, so this averages noise down), then compared
    with the frame `lag` frames earlier (10 = 50 ms), so the movement has time to build up.
    On the bench recording nf6_silent20s this raised speaking-vs-still contrast from +3.1 to
    +9.5 dB (mic 1). For display and activity detection; the trained model expects the
    official feature.
    """
    from scipy.ndimage import uniform_filter1d
    mag = np.abs(uniform_filter1d(prof, avg, axis=-2) if avg > 1 else prof)
    out = np.zeros_like(mag)
    out[..., lag:, :] = mag[..., lag:, :] - mag[..., :-lag, :]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("-s", "--seconds", type=float, default=10)
    ap.add_argument("--channels", default="A", choices=["A", "B", "AB", "none"])
    ap.add_argument("--taper", action="store_true", default=True,
                    help="0.2 ms soft chirp edges (default on: quieter to the ear, same signal quality)")
    ap.add_argument("--no-taper", dest="taper", action="store_false", help="exact official chirp edges")
    ap.add_argument("--level", type=float, default=10.0)
    ap.add_argument("--enhanced", action="store_true", help="also save the enhanced movement picture")
    a = ap.parse_args()

    words, st = record(a.seconds, a.channels, a.taper, a.level)
    gaps = int(np.sum(np.diff(st[:, 0]) != 1))
    print(f"packets {len(st)}, missing {gaps}, dropped {st[-1, 1]}, flags {st[-1, 2]:03b}, "
          f"level A max {st[:, 3].max() / 10:.1f}%")
    skip = int(1.2 * 400_000)  # past the 1 s soft start
    m1 = pdm_to_audio(np.ascontiguousarray(words[skip:][1::2]))
    m2 = pdm_to_audio(np.ascontiguousarray(words[skip:][0::2]))
    n = min(len(m1), len(m2))
    audio = np.stack([m1[:n], m2[:n]], axis=1)
    pcm = np.clip(audio * 32767, -32768, 32767).astype("<i2")
    out = ROOT / "captures"
    out.mkdir(exist_ok=True)
    wav = out / f"sonispeech_{a.label}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes())

    from scipy.signal import welch
    for c, name in enumerate(["mic 1", "mic 2"]):
        f, p = welch(audio[:, c], FS, nperseg=8192)
        lo = 10 * np.log10(p[(f > 200) & (f < 15000)].sum() + 1e-20)
        b0 = 10 * np.log10(p[(f >= 18000) & (f <= 28000)].sum() + 1e-20)
        b1 = 10 * np.log10(p[(f >= 29000) & (f <= 39000)].sum() + 1e-20)
        df = f[1] - f[0]
        # rough SPL: band power -> dBFS (sine-referenced) -> SPL via the datasheet sensitivity
        # (-26 dBFS at 94 dB SPL, specified at 1 kHz; ultrasonic sensitivity may differ by several dB)
        spl = lambda lo_, hi_: 94 + (10 * np.log10(p[(f >= lo_) & (f <= hi_)].sum() * df + 1e-20) + 3) + 26
        print(f"{name}: audible 0.2-15 kHz {lo:6.1f} dB | 18-28 kHz {b0:6.1f} dB | 29-39 kHz {b1:6.1f} dB"
              f" | rough SPL 18-20 kHz {spl(18000, 20000):5.1f} dB, 18-28 kHz {spl(18000, 28000):5.1f} dB")

    align = {"A": [0], "B": [1]}.get(a.channels)  # one amp playing: align on its band only
    prof, start = echo_profiles(audio.astype(float) * 32767 / 32768, read_tx(), align)
    np.save(out / f"sonispeech_{a.label}_profiles.npy", prof.astype(np.float32))
    mag = np.abs(prof)
    print(f"start_pos {start}, frames {prof.shape[2]} ({prof.shape[2] / 200:.1f} s)")
    for n, band in enumerate(["18-28", "29-39"]):
        for c in range(2):
            mean = mag[n, c].mean(axis=0)
            pk = int(np.argmax(mean))
            floor = np.median(mean)
            print(f"  {band} kHz, mic {c + 1}: direct path at bin {pk}, peak/median {20 * np.log10(mean[pk] / floor):5.1f} dB")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    diff = np.abs(prof[:, :, 1:]) - np.abs(prof[:, :, :-1])
    fig, ax = plt.subplots(2, 4, figsize=(16, 7), constrained_layout=True)
    for n, band in enumerate(["18-28 kHz", "29-39 kHz"]):
        for c in range(2):
            m = mag[n, c].T
            ax[n, 2 * c].imshow(m, aspect="auto", origin="lower", cmap="magma",
                                vmax=np.percentile(m, 99.5), extent=[0, m.shape[1] / 200, 0, FRAME])
            ax[n, 2 * c].set(title=f"{band} · mic {c + 1} · |profile|", xlabel="s", ylabel="bin")
            d = diff[n, c].T
            v = np.percentile(np.abs(d), 99)
            ax[n, 2 * c + 1].imshow(d, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v,
                                    extent=[0, d.shape[1] / 200, 0, FRAME])
            ax[n, 2 * c + 1].set(title=f"{band} · mic {c + 1} · differential", xlabel="s")
    png = out / f"sonispeech_{a.label}.png"
    fig.savefig(png, dpi=80)
    print(f"saved {wav.name}, {png.name}")

    if a.enhanced:
        enh = enhanced_diff(prof)
        fig, ax = plt.subplots(2, 1, figsize=(14, 7), constrained_layout=True)
        n = 0 if a.channels in ("A", "AB", "none") else 1
        for c in range(2):
            d = enh[n, c].T[180:360]
            v = np.percentile(np.abs(d), 99.5)
            ax[c].imshow(d, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-v, vmax=v,
                         extent=[0, d.shape[1] / 200, 180, 360])
            ax[c].set(title=f"band {n} · mic {c + 1} · enhanced (avg 4, 50 ms difference)", xlabel="s", ylabel="bin")
        epng = out / f"sonispeech_{a.label}_enhanced.png"
        fig.savefig(epng, dpi=80)
        print(f"saved {epng.name}")


if __name__ == "__main__":
    main()
