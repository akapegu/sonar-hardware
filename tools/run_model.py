#!/usr/bin/env python3
"""Run the trained SoniSpeech model (from the sonar-glasses software repo) on a recording.

    <software>/.venv/bin/python tools/run_model.py --wav captures/sonispeech_nf6_silent20s.wav \
        --t0 7.5 --t1 25 --variant all
    <software>/.venv/bin/python tools/run_model.py --dataset-session <session dir> --n 3   # sanity check

Features follow the software repo exactly: official echo profiles (re-implemented in
tools/sonispeech_capture.py), differential |p_t| - |p_(t-1)|, crop 226:306, paths
[s0m0, s0m1, s1m0, s1m1], per-channel normalisation with the run's stats.json, greedy CTC.

Variants for our glasses recordings (only amp A plays, and our signal is much weaker):
  official   exactly as for the dataset
  gain       each channel scaled so its spread matches the dataset's (our echoes are ~30 dB weaker)
  gain_dup   gain + band-1 paths (s1*) replaced by copies of band-0 paths (amp B is silent)
  smooth     gain_dup + 4-frame average of |profile| before the difference
"""
import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sonispeech_capture import echo_profiles, read_tx  # noqa: E402

CROP = (226, 306)


def load_wav(p):
    with wave.open(str(p)) as w:
        assert w.getframerate() == 100_000 and w.getnchannels() == 2
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(float) / 32768.0


def features(prof, variant, smooth=False):
    from scipy.ndimage import uniform_filter1d
    p = np.abs(prof)  # (2 tx, 2 mic, frames, 500)
    if smooth:
        p = uniform_filter1d(p, 4, axis=2)
    d = np.zeros_like(p)
    d[:, :, 1:] = p[:, :, 1:] - p[:, :, :-1]
    x = d.reshape(4, d.shape[2], -1)[:, :, CROP[0]:CROP[1]].transpose(0, 2, 1)  # (4, 80, T)
    if variant in ("gain_dup", "smooth"):
        x[2], x[3] = x[0].copy(), x[1].copy()
    return x.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--software", default=str(Path("~/Downloads/sonispeech_datasets").expanduser()))
    ap.add_argument("--run", default="runs/final_cc_final")
    ap.add_argument("--ckpt", default="epoch149.pt")
    ap.add_argument("--wav")
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, default=None)
    ap.add_argument("--align", default=None, help="'A' to align on band 0 only (one amp playing)")
    ap.add_argument("--variant", default="official", choices=["official", "gain", "gain_dup", "smooth", "all"])
    ap.add_argument("--dataset-session")
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()

    sw = Path(a.software)
    sys.path.insert(0, str(sw / "src"))
    import torch
    import yaml
    from decode import Tokenizer, greedy_decode
    from model import ResNet34CTC, build_model

    run = sw / a.run
    cfg = yaml.safe_load(open(run / "config.yaml"))["cfg"]
    stats = json.load(open(run / "stats.json"))
    mean = np.asarray(stats["mean"], np.float32)[:, None, None]
    std = np.asarray(stats["std"], np.float32)[:, None, None]
    tok = Tokenizer(run / "tokenizer.model")
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    ck = torch.load(run / a.ckpt, map_location="cpu", weights_only=False)
    model = build_model(cfg["model"], ck["n_classes"])
    model.load_state_dict(ck["model"])
    model.to(dev).eval()

    def decode(x, gain=False):
        if gain:  # scale each channel's spread to the dataset's
            x = x / (x.std(axis=(1, 2), keepdims=True) + 1e-12) * std
        xn = (x - mean) / std
        with torch.no_grad():
            lp = model(torch.from_numpy(xn[None]).to(dev), torch.tensor([xn.shape[2]]))
        return greedy_decode(lp.float().cpu(), ResNet34CTC.out_lengths(torch.tensor([xn.shape[2]])), tok)[0]

    if a.dataset_session:
        s = Path(a.dataset_session)
        audio = load_wav(s / "audio.wav")
        prof, start = echo_profiles(audio, read_tx())
        x = features(prof, "official")
        tr = json.load(open(s / "transcript.json"))
        for k, (uid, u) in enumerate(sorted(tr.items(), key=lambda kv: kv[1]["start"])):
            if k >= a.n:
                break
            f0 = max(0, int(np.floor((u["start"] * 100000 - start) / 500)))
            f1 = int(np.ceil((u["end"] * 100000 - start) / 500))
            print(f"REF: {u['transcript']}\nHYP: {decode(x[:, :, f0:f1])}\n")
        return

    audio = load_wav(a.wav)
    align = [0] if a.align == "A" else None
    prof, start = echo_profiles(audio, read_tx(), align)
    # the WAV starts 1.2 s after recording start (soft start skipped)
    f0 = max(0, int((a.t0 - 1.2) * 200))
    f1 = None if a.t1 is None else int((a.t1 - 1.2) * 200)
    variants = ["official", "gain", "gain_dup", "smooth"] if a.variant == "all" else [a.variant]
    for v in variants:
        x = features(prof, v, smooth=(v == "smooth"))[:, :, f0:f1]
        print(f"{v:9s}: {decode(x, gain=(v != 'official'))!r}")


if __name__ == "__main__":
    main()
