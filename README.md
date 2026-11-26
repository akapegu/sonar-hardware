# sonar-hardware

Hardware for the sonar glasses: a Teensy 4.0 sends ultrasonic FMCW chirps through two
MAX98357A amps and two micro speakers, clocks two PDM MEMS microphones, and streams the raw
echoes to a Mac over USB.

The decoding software (a SoniSpeech reproduction) is in
[akapegu/sonar-glasses](https://github.com/akapegu/sonar-glasses).

**Status (2026-11-26):** both amps and both mics are wired and power-checked (speakers not yet
connected). Experiment 1 (mics only) passed: both mics stream at 3.2 MHz with no dropped data
and respond about 50 dB above the quiet floor to ultrasound from jingling keys. Next: the first
sound on the test speaker (amp A) and a 15–45 kHz sweep.

## Contents

| Path | What it is |
|---|---|
| [docs/wiring.html](docs/wiring.html) | Complete wiring diagram (current bench: separate mic/amp clocks, every resistor, pin map) |
| [docs/rig-handoff.md](docs/rig-handoff.md) | Parts, datasheet links, verified limits, wiring, signal plan, safety rules, bring-up order, open decisions |
| [blink_test/](blink_test/) | Upload sanity check: blinks the onboard LED (pin 13 only) |
| [mic_capture/](mic_capture/) | Experiment 1 firmware: clocks both PDM mics (1.6 MHz start-up, then 3.2 MHz) and streams raw bits over USB. Produces no audio. |
| [tools/mic_capture.py](tools/mic_capture.py) | Mac side: records, checks clock/data health per mic, saves `captures/*.npz` + `.wav`, plots spectra |

## Toolchain (macOS)

- [arduino-cli](https://arduino.github.io/arduino-cli/) (`brew install arduino-cli`)
- Teensy core `teensy:avr` 1.62.0 (board index: `https://www.pjrc.com/teensy/package_teensy_index.json`)

```sh
arduino-cli config add board_manager.additional_urls https://www.pjrc.com/teensy/package_teensy_index.json
arduino-cli core update-index && arduino-cli core install teensy:avr

arduino-cli board list                      # Teensy shows as usb:<id>, FQBN teensy:avr:teensy40
arduino-cli compile --fqbn teensy:avr:teensy40 blink_test
arduino-cli upload  --fqbn teensy:avr:teensy40 -p usb:<id> blink_test
```

### Mic test (Experiment 1)

```sh
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pyserial   # once
arduino-cli upload --fqbn teensy:avr:teensy40 -p usb:<id> mic_capture
.venv/bin/python tools/mic_capture.py -s 10      # jingle keys near the mics while it records
```

Needs numpy, scipy and matplotlib. Each mic should show a ones density of about 0.50, and
200,000 words/s. If one mic shows flat noise at every frequency, reseat its breadboard wires:
that was a loose contact here.

### Troubleshooting

- **The Mac doesn't see the Teensy at all:** try another micro-USB cable before blaming the
  board. Many cables are charge-only or flaky for data, and a bad one looks exactly like a dead
  board (macOS logs `failed to create device` / `persistent enumeration failures`). Test the
  cable with a phone first.
- **An upload hangs with the LED off:** the board is waiting in its bootloader and Teensy Loader
  has stalled. Quit Arduino IDE and Teensy Loader, then run the upload again.

## Safety

The speakers are rated 5 mW. Firmware must cap the amplitude at ≤ 14% of full scale, soft-start
the amplitude, and keep each chirp windowed and zero-mean. Full rules are in
[docs/rig-handoff.md](docs/rig-handoff.md#firmware-safety-rules-non-negotiable).
