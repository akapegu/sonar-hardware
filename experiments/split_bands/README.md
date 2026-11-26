# Experiment: split bands 18–24 / 25–31 kHz

**Why.** Speaker 1's official band (29–39 kHz) arrives ~23–24 dB weaker than 18–28 kHz with our
speakers and shows almost no speech. Moving it to 25–31 kHz (and speaker 0 to 18–24 kHz so the
bands don't overlap) should bring speaker 1 about 10 dB up, at the cost of 6 kHz bandwidth per
band (range step ≈2.9 cm instead of 1.7 cm). The official SoniSpeech mode is unchanged.

**Files.** `split_bands.ino` (copy of `sonispeech_mode.ino` with generated chirps, no dataset
header needed), `split_capture.py` (records, computes profiles with these chirps and band-pass
ranges 17.5–24.5 / 24.5–31.5 kHz, `--test` runs the 10 s silent-speech contrast test).

```sh
arduino-cli upload --fqbn teensy:avr:teensy40 -p usb:<id> experiments/split_bands
.venv/bin/python experiments/split_bands/split_capture.py --label sb_test --test
```

**Baseline** (official mode, same session, 10 s silent counting, enhanced contrast): speaker 0
→ mics +9.9 / +10.0 dB, speaker 1 (29–39 kHz) → mics −0.3 / −0.0 dB.

**Results (2026-11-26).** Same session as the baseline, 10 s of silent counting, enhanced
contrast (speaking vs still):

| Mode | spk 0 → mic 1 | spk 0 → mic 2 | spk 1 → mic 1 | spk 1 → mic 2 |
|---|---|---|---|---|
| Official 18–28 / 29–39 kHz | +9.9 | +10.0 | −0.3 | −0.0 |
| Take turns, both 18–28 kHz | +6.0 | +5.4 | +2.1 | +1.5 |
| **Split 18–24 / 25–31 kHz** | **+11.2** | **+10.0** | **+8.2** | **+6.9** |

Speaker 1 goes from no speech to a clear pattern, and speaker 0 loses nothing to the narrower band,
so all four paths carry speech. **Decision: split bands for the demo data collection** (our own
model; the official mode stays in `sonispeech_mode/` for dataset-matched work).

**Repeat runs (same session).** 10 s of silent counting each; the last two without spoken cues
(`--auto`), which removed the wearer's reaction to the voice and gave the cleanest quiet baseline.

| Run | spk 0 → mic 1 | spk 0 → mic 2 | spk 1 → mic 1 | spk 1 → mic 2 |
|---|---|---|---|---|
| 1 | +11.2 | +10.0 | +8.2 | +6.9 |
| 2 | +8.1 | +7.4 | +3.0 | +2.8 |
| 3 | +11.7 | +10.0 | +10.7 | +9.5 |
| 4 (burst in the quiet window, counting past "stop") | +3.9 | +4.6 | +3.2 | +2.7 |
| 5, no cues | +14.8 | +10.9 | +9.2 | +6.0 |
| 6, no cues | +11.8 | +9.6 | +5.3 | +3.3 |

Speaker 0 paths +8 to +15 dB, speaker 1 paths +3 to +11 dB (always positive). Run 4's picture
shows clear speech on all paths; its numbers were spoiled by movement in the quiet window. Speaker
1's rows sometimes show faint vertical streaks: check its solder joints and amp B's terminal.

