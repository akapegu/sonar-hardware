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

**Results.** (after the test)
