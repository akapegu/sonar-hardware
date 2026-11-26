# Experiment: take turns on 18–28 kHz

**Why.** Our speakers (OWR-0640T-16H) are about 23–24 dB weaker at 29–39 kHz than at 18–28 kHz
(see [docs/rig-handoff.md](../../docs/rig-handoff.md)), so speaker 1's official band carries
little echo and is audibly noisy close to the ear. The trained SoniSpeech model does not transfer
to a new person and device anyway, so we train our own and can choose the signal freely.

**What.** Both speakers play the official 18–28 kHz chirp, taking turns: speaker 0 on even 5 ms
frames, speaker 1 on odd frames. The first 2 s are speaker 0 only, which tells the Mac which frames
belong to which speaker. Each path updates 100 times a second instead of 200, still far faster
than lip movements. The official SoniSpeech mode (`sonispeech_mode/`) is unchanged.

**Files.**

| File | What it is |
|---|---|
| `take_turns.ino` | Copy of `sonispeech_mode.ino` plus the take-turns mode (`'T', 1`) |
| `tdm_capture.py` | Records in take-turns mode, splits frames per speaker, saves WAV, profiles and a picture; `--test` runs the 10 s silent-speech contrast test |
| `tx_chirps.h` | Generated, git-ignored: `python3 tools/make_tx_header.py <dataset tx_signals> experiments/take_turns/tx_chirps.h` |

```sh
arduino-cli compile --fqbn teensy:avr:teensy40 experiments/take_turns
arduino-cli upload  --fqbn teensy:avr:teensy40 -p usb:<id> experiments/take_turns
.venv/bin/python experiments/take_turns/tdm_capture.py --label tt_test --test
```

**Baseline to beat** (official mode, both speakers, best glasses position, 10 s silent counting,
enhanced contrast speaking vs still): speaker 0 → mics +10.4 / +9.4 dB, speaker 1 (29–39 kHz)
→ mics +4.7 / +4.7 dB.

**Results.** (filled in after the test)
