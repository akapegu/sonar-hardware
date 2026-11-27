#!/usr/bin/env python3
"""First experiment: isolated silent digits, one wearer, leave-one-session-out.

Uses the clips from build_dataset.py (enhanced features, 4 paths x 80 bins x frames at 200 Hz).
Model: ResNet-18 (torchvision) with a 4-channel input, as EchoSpeech's encoder, but with a plain
10-way classifier instead of CTC because every clip holds exactly one word. Augmentation follows
EchoSpeech: random placement inside the padded window and amplitude scaling x U(0.95, 1.05).

    ~/Downloads/sonispeech_datasets/.venv/bin/python experiments/echospeech/train_digits.py
    ... --person pegu --epochs 60 --mode enhanced

Each fold trains on all sessions but one and tests on the held-out session, so every number is
measured after taking the glasses off and putting them back on.
"""
import argparse
import glob
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torchvision

ROOT = Path(__file__).resolve().parents[2]
T = 512  # frames (2.56 s); clips are 280-486 frames


def load(person, mode):
    sessions = []
    for f in sorted(glob.glob(str(ROOT / f"captures/echospeech/{person}/s*/clips_{mode}.npz"))):
        sdir = Path(f).parent
        if json.load(open(sdir / "events.json")).get("practice"):
            continue
        d = np.load(f, allow_pickle=True)
        sessions.append((sdir.name, list(d["clips"]), list(d["labels"])))
    return sessions


def fit_window(x, rng=None):
    """Place a (4, 80, t) clip into a (4, 80, T) window: random offset when training, centred otherwise."""
    t = x.shape[2]
    out = np.zeros((4, 80, T), np.float32)
    if t >= T:
        a = (t - T) // 2 if rng is None else rng.integers(0, t - T + 1)
        out[:] = x[:, :, a:a + T]
    else:
        a = (T - t) // 2 if rng is None else rng.integers(0, T - t + 1)
        out[:, :, a:a + t] = x
    return out


def model(n_classes):
    m = torchvision.models.resnet18(num_classes=n_classes)
    m.conv1 = nn.Conv2d(4, 64, kernel_size=7, stride=2, padding=3, bias=False)
    return m


def run_fold(train, test, classes, args, dev, seed):
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    cidx = {c: i for i, c in enumerate(classes)}
    Xtr = [x.astype(np.float32) for x, _ in train]
    ytr = np.array([cidx[y] for _, y in train])
    scale = np.sqrt(np.mean([np.mean(x ** 2, axis=(1, 2)) for x in Xtr], axis=0))[:, None, None]  # per path
    net = model(len(classes)).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)
    steps = args.epochs * int(np.ceil(len(Xtr) / args.batch))
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    lossf = nn.CrossEntropyLoss(label_smoothing=0.1)
    for ep in range(args.epochs):
        net.train()
        for b in np.array_split(rng.permutation(len(Xtr)), int(np.ceil(len(Xtr) / args.batch))):
            xb = np.stack([fit_window(Xtr[i] / scale * rng.uniform(0.95, 1.05), rng) for i in b])
            xb = torch.from_numpy(xb).to(dev)
            loss = lossf(net(xb), torch.from_numpy(ytr[b]).to(dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
    net.eval()
    with torch.no_grad():
        xt = torch.from_numpy(np.stack([fit_window(x.astype(np.float32) / scale) for x, _ in test])).to(dev)
        pred = net(xt).argmax(1).cpu().numpy()
    return pred, np.array([cidx[y] for _, y in test])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default="pegu")
    ap.add_argument("--mode", default="enhanced", choices=["enhanced", "plain"])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    sessions = load(args.person, args.mode)
    classes = sorted({y for _, _, ys in sessions for y in ys})
    print(f"{len(sessions)} sessions, {sum(len(c) for _, c, _ in sessions)} clips, classes {classes}, device {dev}")
    conf = np.zeros((len(classes), len(classes)), int)
    accs = []
    for k, (name, clips, labels) in enumerate(sessions):
        t0 = time.time()
        train = [(x, y) for j, (_, c, l) in enumerate(sessions) if j != k for x, y in zip(c, l)]
        pred, true = run_fold(train, list(zip(clips, labels)), classes, args, dev, args.seed + k)
        for p, t in zip(pred, true):
            conf[t, p] += 1
        accs.append((pred == true).mean())
        print(f"  held out {name}: {accs[-1] * 100:5.1f}%  ({time.time() - t0:.0f} s)", flush=True)
    print(f"mean accuracy {np.mean(accs) * 100:.1f}% (std {np.std(accs) * 100:.1f}), chance {100 / len(classes):.0f}%")
    w = max(len(c) for c in classes)
    print("confusion (rows = said, cols = predicted):")
    print(" " * (w + 1) + " ".join(c[:3].rjust(3) for c in classes))
    for c, r in zip(classes, conf):
        print(c.rjust(w) + " " + " ".join(f"{v:3d}" for v in r))


if __name__ == "__main__":
    main()
