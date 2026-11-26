# Sonar glasses rig: parts, docs, wiring, safety

This is a bench prototype on a breadboard. Nothing is mounted on the glasses yet.

The software that decodes the echoes (a SoniSpeech reproduction) lives in
[akapegu/sonar-glasses](https://github.com/akapegu/sonar-glasses). It is trained on SoniSpeech
data recorded with 18–28 / 29–39 kHz chirps, which is why this rig's bands matter (see
[Open decisions](#open-decisions)).

Limits marked ✔︎ were checked against the datasheets on 2026-11-25. The
[Changes from the original hand-off](#changes-from-the-original-hand-off) section lists what changed.

## Parts

| # | Part | Role | Qty (used / spare) |
|---|---|---|---|
| 1 | Teensy 4.0 (NXP i.MX RT1062, 600 MHz), headers pre-soldered | Generates chirps over I2S, clocks and reads the PDM mics, streams to the Mac over USB | 1 |
| 2 | SmartElex I2S Audio Breakout, MAX98357A, screw terminal | Class D amp, one per speaker | 2 / 1 |
| 3 | Ole Wolff OWR-0640T-16H, 16 Ω micro speaker, 6 mm diameter | Ultrasonic emitters | 2 / 2 (one of the two **soldered** speakers has a heat-damaged side: use it as the test speaker on amp A; the two spares are unsoldered) |
| 4 | Elecrow "Digital MEMS Microphone" board, SKU CCB50641P (Knowles SPH0641LU4H-1, PDM, ultrasonic mode) | Echo receivers | 2 / 1 |
| 5 | Resistors: 2 × 100 kΩ (GAIN), 22 kΩ + 10 kΩ (amp B SD divider), or 1 × 220 kΩ instead of the divider | Gain and channel select | — |
| 6 | Capacitors: 1 × 10–100 µF electrolytic (3.3 V rail bulk), 0.1 µF ceramics if the mic boards lack them | Decoupling | — |
| 7 | Breadboard, jumpers, micro-USB **data** cable (Ambrane, verified), DT830D multimeter | Bench | — |

## Docs

**Teensy 4.0**
- Product page: https://www.pjrc.com/store/teensy40.html
- Pinout card front / back: https://www.pjrc.com/teensy/card10a_rev2_web.pdf · https://www.pjrc.com/teensy/card10b_rev1_web.pdf
- i.MX RT1060 reference manual (SAI = I2S peripheral, clocks): https://www.pjrc.com/teensy/IMXRT1060RM_rev3.pdf
- i.MX RT1060 datasheet (electrical): https://www.pjrc.com/teensy/IMXRT1060CEC_rev4.pdf
- Teensy Audio Library: https://www.pjrc.com/teensy/td_libs_Audio.html · source https://github.com/PaulStoffregen/Audio
- I2S pin mapping on Teensy 4 (MCLK 23, BCLK 21, LRCLK 20, OUT1A 7, IN1 8): https://www.pjrc.com/store/teensy3_audio.html
- Second I2S input lane (pin 6 = SAI1 RX_DATA1): `input_i2s_quad.cpp` in the Audio library
- Teensy Loader CLI: https://www.pjrc.com/teensy/loader_cli.html
- Arduino boards URL: https://www.pjrc.com/teensy/package_teensy_index.json

**MAX98357A amp**
- Datasheet: https://www.analog.com/media/en/technical-documentation/data-sheets/max98357a-max98357b.pdf (mirror: https://cdn-shop.adafruit.com/product-files/3006/MAX98357A-MAX98357B.pdf)
- Board we have: https://robocraze.com/products/smartelex-max98357a-i2s-audio-breakout-amplifier-for-raspberry-pi-and-microcontrollers
- Similar breakouts (schematics, SD/GAIN behaviour): https://learn.sparkfun.com/tutorials/i2s-audio-breakout-hookup-guide/all · https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp

**Microphone**
- Board: https://www.elecrow.com/digital-mems-microphone.html (sound hole = circled ◎ on the back of the board)
- SPH0641LU4H-1 datasheet: https://media.digikey.com/pdf/Data%20Sheets/Knowles%20Acoustics%20PDFs/SPH0641LU4H-1.PDF (mirror: https://akizukidenshi.com/goodsaffix/SPH0641LU4H-1.PDF)

**Speaker**
- Datasheet: https://media.digikey.com/pdf/Data%20Sheets/Ole%20Wolff/OWR-0640T-16H.pdf

## Key limits

| Part | Limit (datasheet) | Our plan | Status |
|---|---|---|---|
| Speaker | 16 Ω ±15%, resonance 3 kHz. Rated 5 mW (0.28 V rms), max 10 mW (0.4 V rms) ✔︎ | Amplitude: start at 10% FS (≈ 2 mW), hard ceiling **14% FS** (≈ 4 mW) | Hard rule |
| Speaker | Frequency range 50 Hz–20 kHz ✔︎ | We drive 18 kHz and up | Output above 20 kHz unknown: measure with the 15–45 kHz sweep |
| MAX98357A | Supply 2.5–5.5 V. LRCLK range 4 is 83.8–100.8 kHz. BCLK must be 32, 48 or 64 × LRCLK ✔︎ | 3.3 V from the Teensy. fs = 100 kHz, BCLK = 32 × fs | In spec |
| MAX98357A | **Digital filter at LRCLK > 50 kHz: flat to 0.24·fs, −3 dB at 0.31·fs, stopband 0.477·fs (60 dB)** ✔︎ | At fs = 100 kHz: flat to 24 kHz, −3 dB at 31 kHz | **29–39 kHz band is attenuated by an unspecified amount: measure** |
| MAX98357A | Gain is relative to 2.1 dBV. GAIN through 100 kΩ to VDD = 3 dB (2.4–3.6 dB), so 0 dBFS ≈ 1.80 V rms ✔︎ | 10% FS ≈ 0.18 V rms, 14% FS ≈ 0.25 V rms | Basis for the amplitude cap |
| MAX98357A | Bridge-tied outputs ✔︎ | Speaker on OUT+ / OUT− only | Never connect either output to GND |
| SPH0641LU4H-1 | VDD 1.62–3.6 V. Ultrasonic mode clock 3.072–4.8 MHz, duty cycle 48–52% above 2.4 MHz ✔︎ | 3.3 V. Mic clock = BCLK = **3.2 MHz** | In spec |
| SPH0641LU4H-1 | **"Do not power up into ultrasonic mode. Do not wake up from sleep mode into ultrasonic mode."** Power-up ≤ 50 ms, wake ≤ 15 ms, mode change ≤ 10 ms ✔︎ | Start at fs = 48 kHz (BCLK 1.536 MHz, standard mode) for ≥ 60 ms, then switch to 100 kHz | Firmware must do this |
| Teensy | 3.3 V I/O, not 5 V tolerant. Recommended external load on the 3V pin ≤ 250 mA ✔︎ | Everything on 3V. Total draw is tens of mA | Never 5 V (VIN) |

The power figures use P = V²/16 Ω for a sine whose peak is the stated percentage of full scale.
At the top of the gain tolerance (+0.6 dB), 14% is ≈ 4.6 mW, still under the 5 mW rating.
Real power is lower still, because the speaker's impedance rises at ultrasonic frequencies and
the amp attenuates above 24 kHz.

## Wiring (breadboard)

Teensy → buses:
- 3V → 3.3 V rail. Put the 10–100 µF capacitor across the 3.3 V rail and GND, near the amps.
- GND → GND rail
- Pin 21 (BCLK1) → amp A BCLK, amp B BCLK, mic 1 CLK, mic 2 CLK
- Pin 20 (LRCLK1) → amp A LRCLK, amp B LRCLK
- Pin 7 (OUT1A) → amp A DIN, amp B DIN
- Pin 8 (IN1, SAI1 RX_DATA0) ← mic 1 DATA
- Pin 6 (SAI1 RX_DATA1) ← mic 2 DATA

VIN (5 V) is the pin nearest the USB socket on the 3V side, with GND between it and 3V. Don't
use it for anything on this rig.

Amp A (left channel, speaker 0):
- VIN → 3.3 V, GND → GND
- SD → 3.3 V (> 1.5 V = left). Driving it directly is fine because the amp runs from the same rail.
- GAIN → 100 kΩ → 3.3 V (3 dB, lowest gain)
- OUT+ / OUT− → speaker 0

Amp B (right channel, speaker 1):
- VIN → 3.3 V, GND → GND
- SD → 22 kΩ to 3.3 V and 10 kΩ to GND. With the chip's internal 100 kΩ pulldown this gives
  ≈ 0.97 V. The right-channel window is 0.825–1.245 V (worst case). Alternative from the
  datasheet: a single 210 kΩ (use 220 kΩ) to 3.3 V gives ≈ 1.06 V. The board has its own SD
  pull-up of unknown value: **measure SD before connecting the speaker.**
- GAIN → 100 kΩ → 3.3 V
- OUT+ / OUT− → speaker 1

Mics (both):
- 3.3 → 3.3 V, GND → GND, CLK → pin 21, SEL → GND
- SEL = GND: the mic drives DATA on the falling clock edge; the host latches on the rising edge ✔︎.
- Check for a 0.1 µF capacitor close to each mic's 3.3 V pin; add one if it's missing.
- The header was soldered on the component side so the ◎ sound hole faces up. Pin order as
  mounted: 3.3, GND, CLK, DATA, SEL.

## Signal plan

- I2S out at fs = 100 kHz with **16-bit** stereo frames → BCLK = 100 kHz × 2 × 16 = **3.2 MHz**.
  This is also the mic PDM clock (ultrasonic mode, in spec), and 32 × fs is a supported amp ratio.
- Start-up: run at fs = 48 kHz (BCLK 1.536 MHz) sending zeros for ≥ 60 ms so the mics wake in
  standard mode, then switch to fs = 100 kHz. The amps follow the clock automatically.
- Chirps: each 500 samples = 5 ms, repeated back to back (200 frames/s). Bands: **open
  decision** (see below).
- Mics: raw PDM bits streamed to the Mac over USB (3.2 Mbit/s per mic). Decimate ÷32 → 100 kHz
  on the Mac, then cross-correlate with each chirp → echo profiles.
- Unambiguous range with 5 ms back-to-back chirps ≈ 0.86 m. Reflections from farther away
  wrap into the window.

## Firmware safety rules (non-negotiable)

1. Hard-coded amplitude cap: start at 10% of full scale, never exceed **14%**, set by a
   compile-time constant that no runtime command can override. The cap applies to the final
   samples sent to the amp, after any shaping or equalisation.
2. Soft start: ramp amplitude from 0 over ≥ 0.5 s at power-up and after any restart.
3. Window each chirp's edges (short fade-in and fade-out) and keep the waveform zero-mean (no DC
   into the speaker).
4. First run on the heat-damaged (soldered) test speaker only.
5. No speaker connected until both amps' SD voltages are measured (A ≈ 3.3 V, B ≈ 0.97 V or
   ≈ 1.06 V with the 220 kΩ option).
6. Mic start-up order: standard-mode clock first, ultrasonic second (see the signal plan).

Fail-safe built into the amp: if BCLK stops (Teensy reset, upload, crash), the MAX98357A goes to
standby and its outputs go high-impedance.

## Bring-up order

1. Teensy alone: upload Blink → visible blink. **Done 2026-11-25** (see the main README for the toolchain).
2. Multimeter, unpowered: speakers read 12–16 Ω; no continuous beep between neighbouring pins;
   no short between 3.3 V and GND.
3. Wire everything except the speakers and mic 2. Power up: measure 3.3 V, then SD on each amp.
4. Flash the firmware (cap 10%). Connect the test speaker to amp A only.
5. Add mic 1. On the Mac: the spectrogram shows energy in speaker 0's band, and the
   cross-correlation peaks every 500 samples.
6. **Sweep 15–45 kHz at 10% FS** to measure the real output of the amp and speaker together. This
   decides the bands.
7. Add speaker 1 and mic 2. Check speaker 1's band.
8. Card test: move a card toward and away from the mic. The echo peak should shift **1 sample per
   ≈ 1.7 mm of card movement** (the round trip is 3.43 mm of path per sample at 100 kHz).

## Open decisions

**Chirp bands.** SoniSpeech, the dataset the software is trained on, uses speaker 0 at
18–28 kHz and speaker 1 at 29–39 kHz. At fs = 100 kHz the MAX98357A is −3 dB at 31 kHz, and
its response between 31 and 47.7 kHz isn't specified, so 29–39 kHz will come out weaker by an
unknown amount. Options:

1. **Keep 18–28 / 29–39** (compatible with the dataset). Acceptable if the sweep shows the loss
   at 29–39 kHz is tolerable. If it isn't, speaker 1 needs a different output path that supports
   higher sample rates, for example a 192 kHz I2S DAC plus a small amp.
2. **18–24 / 25–31 kHz**: both bands within the amp's flat-to-−3 dB region. ≈ 2.9 cm range
   resolution, but no longer matched to the dataset.
3. **Speakers take turns over 18–31 kHz**: more bandwidth per chirp (≈ 1.3 cm), half the frame
   rate per speaker, also not matched to the dataset.

Decide after the sweep (bring-up step 6). Don't compensate for the amp's attenuation by boosting
the digital signal above the amplitude cap.

## Changes from the original hand-off

- Amplitude ceiling 20% → **14%**. 20% FS is ≈ 8 mW (up to ≈ 9.3 mW with gain tolerance), above
  the speaker's 5 mW rating.
- I2S frames 32-bit → **16-bit**, so BCLK is 6.4 → **3.2 MHz**. 6.4 MHz was 33% above the mic's
  ultrasonic-mode maximum. Mac decimation ÷64 → ÷32.
- Added the mic start-up rule (standard mode before ultrasonic), which is required by the datasheet.
- fs = 100 kHz is **in spec** for the MAX98357A (LRCLK range 4 is 83.8–100.8 kHz), not "slightly over".
- Found the amp's high-rate filter roll-off (−3 dB at 31 kHz). The 29–39 kHz band is now an open
  decision, and the sweep moved earlier in the bring-up order.
- Pin 6 confirmed as SAI1 RX_DATA1, so it's no longer provisional.
- Card test: 1 sample = ≈ 1.7 mm of card movement, not 3.43 mm (the echo path is a round trip).
- Amp B SD: documented the expected voltage (≈ 0.97 V) and the single-resistor alternative from
  the datasheet. Corrected the VIN/3V adjacency note.
- Added decoupling capacitors to the parts and wiring.

## Bench notes: long mic wires (2026-11-26)

Both mics hang off ~1 m wire bundles (mic 1 braided, mic 2 straight and a bit longer) and share
the 3.2 MHz BCLK on pin 21. With the clock wired straight to both, no single pin-21 drive
strength worked for both mics: a strong clock rang on mic 2's straight bundle (it miscounted
ticks), a weak one was too slow for mic 1's braided bundle. Moving the sampling edge did not
help, which showed the problem was the clock at the far end, not data timing.

Fix: a **47 Ω series resistor** from the Teensy pin 21 row to mic 2's CLK wire, placed at the
Teensy end; mic 1's CLK stays direct. With it, both mics are clean (about −80 dB, 5–45 kHz,
quiet room) at drive strength 6 (chip default, SPEED 2), stable over 10 s; strength 4 also
works with less margin. Re-run `tools/pad_scan.py` (needs speaker_sweep, speaker disconnected)
whenever the mic wiring changes, e.g. on the glasses.

### Amps moved to their own clock pins (2026-11-26)

With both mic clock wires on the shared BCLK (even behind 47 Ω resistors), the amps stayed muted:
a 1 kHz tone and the sweep were silent, and unplugging the mic clock wires made the sweep
audible straight away. The amps now run from the Teensy's second audio port (SAI2), clocked from
the same PLL4 with the same dividers, so playback and recording run at exactly the same rate:

| Signal | Teensy pin | Goes to |
|---|---|---|
| Mic BCLK (SAI1) | 21 | 47 Ω → mic 1 CLK, 47 Ω → mic 2 CLK |
| Mic 1 DATA | 6 | mic 1 (braided bundle) |
| Mic 2 DATA | 8 | mic 2 (straight bundle) |
| Amp BCLK (SAI2) | **4** | amp A + amp B BCLK |
| Amp LRCLK | **3** | amp A + amp B LRC |
| Amp DIN | **2** | amp A + amp B DIN |
| (unused) | 7, 20 | nothing |

This supersedes the pin 20/7 amp wiring above. Result: amp A + a good speaker plays the sweep,
both mics are clean (about −82 dB floor) and hear it.

First response reading (good speaker 10 cm from mic 1, 10% level, 15–45 kHz sweep), dB above the
no-speaker floor: 15–25 kHz about +20, 25–30 kHz +17, 30–32.5 kHz +11, 32.5–35 kHz +6,
35–40 kHz +1 to +3, above 40 kHz nothing. That is far steeper than the amp filter alone (−3 dB at
31 kHz), so the speaker is likely the main limit for the 29–39 kHz band. Not yet a careful
measurement (SNR limited, one position).

The heat-damaged test speaker gave no sound in the earlier runs, but those were all with the
muted amp. Re-tested after the clock fix: it plays, with nearly the same response as the good
speaker (15–20 kHz about +20 to +24 dB above the floor, 30–35 kHz +6 to +9, 35–40 kHz about +2).
Both soldered speakers work; the steep roll-off above 30 kHz looks like a property of this
speaker model.

### Measured response, 15–45 kHz (2026-11-26)

`tools/sweep_measure.py`, synchronous average of 92 sweeps (playback and recording share a
clock), heat-damaged test speaker on amp A about 8–10 cm from both mics, 10% level. Response
relative to 18–20 kHz (SNR after averaging 37–68 dB in 15–42 kHz):

| kHz | 15–18 | 18–20 | 20–26 | 26–30 | 30–34 | 34–36 | 36–40 | 40–42 | 42–45 |
|---|---|---|---|---|---|---|---|---|---|
| mic 1 | +3.5 | 0 | −3.6 to +0.2 | −12 to −13 | −24 to −25 | −27 | −33 to −36 | −42 | −51 |
| mic 2 | +4.5 | 0 | −3.2 to +0.1 | −10 to −15 | −24 to −26 | −30 | −31 to −33 | −37 | −44 |

SoniSpeech bands: 18–28 kHz averages −3 to −4 dB, 29–39 kHz averages −26 to −27 dB, so the
upper band is about 23–24 dB weaker. Whether the amp or the speaker causes the drop was settled
by the direct-drive test below. The 14% cap leaves only +3 dB of headroom, and the slope
inside 29–39 kHz is about 23 dB, so pre-emphasis cannot flatten it. Next: build SoniSpeech mode
with the original bands and judge the 29–39 kHz echo profiles directly before changing bands or
parts.

### Speaker alone vs through the amp (2026-11-26)

`tone_direct/` drives the same speaker straight from Teensy pins 14/15 through 1.5 kΩ (opposite
phase, no DC, ~0.1 mW), bypassing the amp; `tools/tone_direct_test.py` compares it with the amp
sweep. Relative to 20 kHz (mic 1 / mic 2):

| Tone | Speaker alone | Through amp |
|---|---|---|
| 24 kHz | −0.7 / −2.3 | +2.0 / −0.2 |
| 28 kHz | −15.6 / −12.9 | −10.8 / −14.2 |
| 32 kHz | −26.4 / −27.7 | −24.9 / −23.4 |
| 36 kHz | −22.2 / −24.6 | −28.4 / −27.3 |
| 40 kHz | −23.5 / −19.3 | −36.1 / −35.2 |

Verdict: the **speaker** causes most of the 29–39 kHz loss (13–28 dB, with a dip near 32 kHz);
the amp filter only adds ~3–6 dB at 36 kHz and ~12–16 dB at 40 kHz. A better amp alone would not
fix the band; a speaker designed for ultrasound would, ideally together with a 192 kHz-capable
DAC/amp for the top few kHz. First check whether the current band still gives usable face echoes.

