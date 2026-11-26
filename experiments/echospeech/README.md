# Experiment: EchoSpeech-style command recognition (demo, 29 Nov)

Follows EchoSpeech (Zhang et al., CHI '23; `~/Downloads/echospeech.pdf`) as closely as our rig allows.
The SoniSpeech model does not transfer to a new wearer and device, so the demo recognises a fixed
command set trained on the wearer's own data, as EchoSpeech does.

| | EchoSpeech | Here |
|---|---|---|
| Vocabulary | 31 commands, 32 word labels (Table 1) | same, [commands.py](commands.py) |
| Session | each command ×4, random order, ≤3 s auto-advance, space = next, x = redo; remount between sessions; session 1 = familiarisation | each command ×4, random order, push-to-record (space start/stop, ≤4 s), x = redo; participants and numbered sessions; live echo picture ([collect_server.py](collect_server.py), [collect.html](collect.html), [live_view.py](live_view.py)) |
| Signal | 2 speakers on separate bands (18–21 / 21.5–24.5 kHz), 50 kHz, 12 ms sweeps, 4 paths | split bands 18–24 / 25–31 kHz, 100 kHz, 5 ms sweeps, 4 paths ([../split_bands](../split_bands)) |
| Features | differential echo profiles, 4 channels | same |
| Model | ResNet-18 + CTC, range-axis pooling, one output per 16 frames; Adam lr 2e-4, cosine, batch 5 | same (to do) |
| Augmentation | merge consecutive utterances, random scaling, random padding | same (to do) |
| Training | pre-train on other users (100 epochs), fine-tune on 2 sessions (15 epochs) | our sessions only (one wearer), optionally initialised from the SoniSpeech ResNet |
| Live | sliding window ≈2.3 s, stride 16 frames, majority vote | same (to do) |
| Sync | hand clap in video and audio | not needed: prompts are time-stamped in recording samples |

## Collecting

1. Teensy runs `experiments/split_bands` (both speakers on, 10%, soft edges, 14% hard cap).
2. `.venv/bin/python experiments/echospeech/collect_server.py`, open http://localhost:8765
   (`--dry` runs the page without the Teensy).
3. Start screen: participant ID, command set (all 31, one group, or a custom list), repeats per
   command (default 4), practice tick box; the right side lists that person's sessions with a
   Delete button (two clicks; the session is moved to `captures/echospeech/_trash/`, not erased).
4. Recording screen: the word to mouth on top, a live enhanced echo picture of all 4 paths below.
   **space** starts a take, **space** ends it (auto-end after 4 s), **x** redoes the word, **p**
   pauses, **q** ends the session early and saves it.
5. Take the glasses off and on between sessions; mark the first session of each person as practice.

Layout: `captures/echospeech/<person>/s<NN>_<date>[_practice]/` with `raw_words.npy` and
`events.json` (person, session number, command set and prompt order, level, firmware, git commit,
and every take with start/end in 100 kHz samples). `build_dataset.py` turns each session into
`clips_enhanced.npz` (default) or `clips_plain.npz`.

## Details from the full paper that matter for this rig

- **Placement (§4.3).** Along the lower rim, the microphone nearest the nose did far worse (47% vs
  ~90%) and so did the speaker nearest the nose. Final layout: both speakers at one outer side, both
  microphones at the other. Our speaker 1 sat near the nose and was the weak one: move nose-side
  parts outward before locking the setup.
- **Amplitude (§8.3, §8.5).** In a quiet room even very low amplitude worked; performance flattened
  at 10–20% of their study level, with more amplitude helping only against loud noise. 100% ≈ 78.6 dB
  SPL at the ear canal, 20% ≈ 62.2 dB SPL; cited long-term limit near 20 kHz: 75–85 dB SPL. Lowering
  our level to cut audibility is consistent with their findings; check contrast at 5% vs 10%.
- **Sweep length (§5.2).** 12 ms sweeps (600 samples at 50 kHz) were best among the lengths they
  tried; bands 18–21 / 21.5–24.5 kHz; microphones ICS-43434 at 50 kHz; amplifier SGTL5000; speakers
  OWR-06049T-38D. We use 5 ms sweeps (our 4-frame coherent average plays a similar role).
- **Biggest error source (§7.2.1, §8.7).** Glasses slipping and being pushed back (worst
  participants); a medium frame fitted best. Collect with a snug fit, never touch the glasses, press
  x if they moved.
- **Speed (§6.4, §7.5).** Mean 1.51 s per word; accuracy dropped below 1.4 s per word. Mouth
  clearly, unhurried.
- **Training/eval (§5.4–5.6, §7.2.1).** No sliding window in training; merge consecutive utterances
  up to 9.6 s; scale ×U(0.95, 1.05); random padding (50% to batch max, 50% random up to 9.6 s);
  CNN only (RNN layers did not help); last-epoch model; 6-fold CV holding out 2 of 12 sessions; WER
  per utterance from sliding-window predictions.
- **Demos (§8.1).** Each live demo used its own small command set with 1–6 min of training data.
- **Robustness (§5.5.4–5.5.5).** Walking/acoustic noise recorded with the device, 1–3 slices ×U(0,1)
  added to training samples.
