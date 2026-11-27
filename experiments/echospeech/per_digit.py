#!/usr/bin/env python3
"""Per-word recall / precision: hold out each listed session, train on all other sessions.

    .venv/bin/python experiments/echospeech/per_digit.py --hold s14 s15 s16 s17 --aug extra
Writes results/per_digit_<tag>.json (true/pred per held-out clip) and prints the confusion matrix.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from train_digits import fit_window, load, train_model

HERE = Path(__file__).resolve().parent


def report(true, pred, classes):
    k = len(classes)
    conf = np.zeros((k, k), int)
    for t, p in zip(true, pred):
        conf[t, p] += 1
    w = max(len(c) for c in classes)
    print(" " * (w + 1) + " ".join(c[:4].rjust(4) for c in classes) + "  recall precision   n")
    for i, c in enumerate(classes):
        n, pc = conf[i].sum(), conf[:, i].sum()
        print(c.rjust(w) + " " + " ".join(f"{v:4d}" for v in conf[i]) +
              f"  {100 * conf[i, i] / max(n, 1):5.0f}%  {100 * conf[i, i] / max(pc, 1):6.0f}%  {n:3d}")
    print(f"overall {100 * np.trace(conf) / conf.sum():.1f}% of {conf.sum()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default="pegu")
    ap.add_argument("--hold", nargs="+", required=True)
    ap.add_argument("--aug", default="extra", choices=["echo", "extra"])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    S = {n: list(zip(c, l)) for n, c, l in load(args.person, "enhanced")}
    classes = sorted({y for v in S.values() for _, y in v})
    cidx = {c: i for i, c in enumerate(classes)}
    out = []
    for k, pre in enumerate(args.hold):
        h = next(n for n in S if n.startswith(pre))
        net, scale = train_model([p for n, v in S.items() if n != h for p in v], classes, args, dev, args.seed + k)
        with torch.no_grad():
            x = torch.from_numpy(np.stack([fit_window(c.astype(np.float32) / scale) for c, _ in S[h]])).to(dev)
            pr = torch.softmax(net(x), 1).cpu().numpy()
        for (_, y), p in zip(S[h], pr):
            out.append(dict(session=h, true=y, pred=classes[int(p.argmax())], conf=float(p.max())))
        print(f"held out {h[:3]}: {np.mean([o['true'] == o['pred'] for o in out if o['session'] == h]) * 100:.1f}%",
              flush=True)
    (HERE / "results").mkdir(exist_ok=True)
    json.dump(out, open(HERE / "results" / f"per_digit{args.tag}.json", "w"), indent=0)
    report([cidx[o["true"]] for o in out], [cidx[o["pred"]] for o in out], classes)


if __name__ == "__main__":
    main()
