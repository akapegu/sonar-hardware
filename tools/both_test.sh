#!/bin/zsh
# usage: tools/both_test.sh LABEL   (both speakers, 10%, 10 s of silent speech, prints contrast per path)
cd "$(dirname "$0")/.."
(python3 -c "import time,subprocess;time.sleep(0.5);subprocess.run(['say','get ready']);time.sleep(4.5);subprocess.run(['say','move']);time.sleep(9.5);subprocess.run(['say','stop'])" &)
.venv/bin/python tools/sonispeech_capture.py --label $1 -s 20 --channels AB --level 10 2>&1 | grep -E "flags"
.venv/bin/python - $1 <<'PY'
import sys, numpy as np
sys.path.insert(0,'tools')
from sonispeech_capture import enhanced_diff
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
lab=sys.argv[1]
P=np.load(f'captures/sonispeech_{lab}_profiles.npy'); t=1.2+np.arange(P.shape[2])/200; E=enhanced_diff(P)
names=[(0,0,'spk 0 (18-28) → mic 1'),(0,1,'spk 0 (18-28) → mic 2'),(1,0,'spk 1 (29-39) → mic 1'),(1,1,'spk 1 (29-39) → mic 2')]
fig,ax=plt.subplots(4,1,figsize=(12,10),constrained_layout=True); row=[]
for r,(n,c,title) in enumerate(names):
    en=np.abs(E[n,c])[:,226:306].sum(1)
    row.append(f"{20*np.log10(en[(t>=6.5)&(t<15)].mean()/en[(t>=2.5)&(t<5)].mean()):+5.1f}")
    z=E[n,c][1:,180:360].T; v=np.percentile(np.abs(z),99.5)
    ax[r].imshow(z,aspect='auto',origin='lower',cmap='RdBu_r',vmin=-v,vmax=v,extent=[1.2,1.2+z.shape[1]/200,180,360])
    for x in (5.6,15.2): ax[r].axvline(x,color='k',lw=.8,ls='--')
    for y in (226,306): ax[r].axhline(y,color='g',lw=.5)
    ax[r].set(title=f"{title} · enhanced",xlabel='s',ylabel='bin')
print(f"{lab}: enhanced speech contrast  s0m1 {row[0]}  s0m2 {row[1]}  s1m1 {row[2]}  s1m2 {row[3]} dB")
fig.suptitle(f'{lab} · both speakers 10% · 10 s silent counting (dashed: move / stop)')
fig.savefig(f'captures/{lab}_paths.png',dpi=60)
PY
open captures/$1_paths.png
