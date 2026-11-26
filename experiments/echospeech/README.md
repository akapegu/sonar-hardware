# Experiment: EchoSpeech-style command recognition (demo, 29 Nov)

Follows EchoSpeech (Zhang et al., CHI '23; `~/Downloads/echospeech.pdf`) as closely as our rig allows.
The SoniSpeech model does not transfer to a new wearer and device, so the demo recognises a fixed
command set trained on the wearer's own data, as EchoSpeech does.

| | EchoSpeech | Here |
|---|---|---|
| Vocabulary | 31 commands, 32 word labels (Table 1) | same, [commands.py](commands.py) |
| Session | each command ×4, random order, ≤3 s, space = next, x = redo; remount between sessions; session 1 = familiarisation | same, [collect_server.py](collect_server.py) + [collect.html](collect.html) |
| Signal | 2 speakers on separate bands (18–21 / 21.5–24.5 kHz), 50 kHz, 12 ms sweeps, 4 paths | split bands 18–24 / 25–31 kHz, 100 kHz, 5 ms sweeps, 4 paths ([../split_bands](../split_bands)) |
| Features | differential echo profiles, 4 channels | same |
| Model | ResNet-18 + CTC, range-axis pooling, one output per 16 frames; Adam lr 2e-4, cosine, batch 5 | same (to do) |
| Augmentation | merge consecutive utterances, random scaling, random padding | same (to do) |
| Training | pre-train on other users (100 epochs), fine-tune on 2 sessions (15 epochs) | our sessions only (one wearer), optionally initialised from the SoniSpeech ResNet |
| Live | sliding window ≈2.3 s, stride 16 frames, majority vote | same (to do) |
| Sync | hand clap in video and audio | not needed: prompts are time-stamped in recording samples |

## Collecting

1. Teensy runs `experiments/split_bands` (both speakers on, 10%, soft edges).
2. `.venv/bin/python experiments/echospeech/collect_server.py`, open http://localhost:8765.
3. Enter starts a session (124 prompts, about 5–6 min). Mouth each word silently, slightly larger
   than speaking aloud; space when done, x to redo, p to pause.
4. Take the glasses off and on between sessions. Session 1 is practice.

Sessions land in `captures/echospeech/<id>/` (`raw_words.npy`, `events.json`; git-ignored).
