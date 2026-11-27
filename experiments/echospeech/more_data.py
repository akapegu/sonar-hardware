#!/usr/bin/env python3
"""How much does more data help? Hold out each live-tested session in turn and train on the base
sessions plus k of the other new sessions (k = 0..n-1). k = 0 is the live model's situation
(one model, tested on every new session).

    .venv/bin/python experiments/echospeech/more_data.py --base s02 s03 s04 s05 s06 s07 --new s09 s10 s11 s12
"""
import argparse
import time

import numpy as np
import torch

from train_digits import fit_window, load, train_model


def accuracy(net, scale, test, classes, dev):
    cidx = {c: i for i, c in enumerate(classes)}
    with torch.no_grad():
        x = torch.from_numpy(np.stack([fit_window(c.astype(np.float32) / scale) for c, _ in test])).to(dev)
        pred = net.to(dev)(x).argmax(1).cpu().numpy()
    return (pred == np.array([cidx[y] for _, y in test])).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default="pegu")
    ap.add_argument("--mode", default="enhanced")
    ap.add_argument("--base", nargs="+", required=True, help="session name prefixes always used for training")
    ap.add_argument("--new", nargs="+", required=True, help="session name prefixes held out in turn")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--aug", default="echo", choices=["echo", "extra"])
    args = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    S = {name: list(zip(c, l)) for name, c, l in load(args.person, args.mode)}
    pick = lambda pre: next(n for n in S if n.startswith(pre))  # noqa: E731
    base, new = [pick(p) for p in args.base], [pick(p) for p in args.new]
    classes = sorted({y for n in base + new for _, y in S[n]})
    pairs = lambda names: [p for n in names for p in S[n]]  # noqa: E731
    res = {}
    t0 = time.time()
    net, scale = train_model(pairs(base), classes, args, dev, args.seed)
    res[0] = [accuracy(net, scale, S[h], classes, dev) for h in new]
    print(f"{len(base)} sessions (+0 new): " + "  ".join(f"{h[:3]} {a * 100:5.1f}%" for h, a in zip(new, res[0]))
          + f"  ({time.time() - t0:.0f} s)", flush=True)
    for k in range(1, len(new)):
        res[k] = []
        for i, h in enumerate(new):
            others = [n for n in new if n != h]
            extra = [others[(i + j) % len(others)] for j in range(k)]  # rotate which new sessions are added
            t0 = time.time()
            net, scale = train_model(pairs(base + extra), classes, args, dev, args.seed + k)
            res[k].append(accuracy(net, scale, S[h], classes, dev))
            print(f"{len(base) + k} sessions (+{k} new: {' '.join(e[:3] for e in extra)}) -> {h[:3]}: "
                  f"{res[k][-1] * 100:5.1f}%  ({time.time() - t0:.0f} s)", flush=True)
    print()
    for k, a in sorted(res.items()):
        print(f"{len(base) + k:2d} training sessions ({k} from the new batch): mean {np.mean(a) * 100:5.1f}%"
              f"  [{' '.join(f'{x * 100:.0f}' for x in a)}]")


if __name__ == "__main__":
    main()
