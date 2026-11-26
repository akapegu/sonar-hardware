// Experiment 2: first sound. Test speaker on amp A + 15-45 kHz sweep, both mics recording.
//
// What it plays: a 15 -> 45 kHz linear chirp lasting 100 ms, then 100 ms of silence, repeated
// (5 chirps per second). Each chirp has 5 ms raised-cosine fade-in/out and is zero-mean.
// Left channel only (amp A, SD = 3.3 V). The right channel (amp B) is always exactly 0.
//
// SAFETY (docs/rig-handoff.md, "Firmware safety rules"):
//   * LEVEL_START = 10% of full scale; LEVEL_CAP = 14% is a compile-time hard limit, checked by
//     static_assert, and every final sample is clamped to it. No runtime command changes it.
//   * Sound plays ONLY while the Mac has the USB port open (the capture script is running).
//     Level ramps up from 0 over 1.0 s when the port opens, and down to 0 over 0.1 s when it
//     closes. After any reset or upload it starts silent.
//   * The amps only get clocks after the mics are in ultrasonic mode; before that, pins 4 (BCLK),
//     3 (LRC) and 2 (DIN) are held LOW. If the Teensy crashes or resets, BCLK stops and the amp
//     goes to standby (high-impedance outputs).
//   * Average speaker power at 10%: about 2 mW peak, about 1 mW average (rated 5 mW).
//
// Two separate clock outputs (2026-11-26): the long mic wires on the shared BCLK made the amps
// mute, so the mics and the amps now use different SAI ports:
//   mics: SAI1 receiver, BCLK pin 21, DATA pins 8 (RX_DATA0) and 6 (RX_DATA1)
//   amps: SAI2 transmitter, BCLK pin 4, LRCLK pin 3, DIN pin 2 (16-bit I2S, 3.2 MHz / 100 kHz)
// Both ports divide the same PLL4 by the same ratio, so playback and recording run at exactly
// the same rate (fixed phase offset per boot, which echo processing aligns anyway). USB packets are the same "SNR1" format; byte 30-31 carries the current level in
// tenths of a percent (100 = 10.0%).

#include <Arduino.h>

static const uint8_t PIN_AMP_BCLK = 4;   // SAI2_TX_BCLK
static const uint8_t PIN_AMP_LRCLK = 3;  // SAI2_TX_SYNC
static const uint8_t PIN_AMP_DIN = 2;    // SAI2_TX_DATA

// ---- Output safety limits (compile-time only) ----
static constexpr float LEVEL_START = 0.10f;  // playback level, fraction of full scale
static constexpr float LEVEL_CAP = 0.14f;    // hard limit, never exceeded
static_assert(LEVEL_START > 0.0f && LEVEL_START <= LEVEL_CAP, "start level above cap");
static_assert(LEVEL_CAP <= 0.14f, "cap must stay <= 14% (speaker rated 5 mW)");
static constexpr float RAMP_UP_S = 1.0f;     // >= 0.5 s required
static constexpr float RAMP_DOWN_S = 0.1f;
static_assert(RAMP_UP_S >= 0.5f, "soft start must be >= 0.5 s");
static constexpr int32_t SAMPLE_LIMIT = (int32_t)(LEVEL_CAP * 32767.0f);

// ---- Sweep ----
static constexpr float FS = 100000.0f;
static constexpr float F0 = 15000.0f, F1 = 45000.0f;
static constexpr uint32_t CHIRP_N = 10000;   // 100 ms
static constexpr uint32_t PERIOD_N = 20000;  // 200 ms (chirp + silence)
static constexpr uint32_t FADE_N = 500;      // 5 ms raised-cosine edges
DMAMEM static float sweep[PERIOD_N];         // normalised to peak 1.0
// Audible check: 1 kHz sine (exactly 100 samples per cycle, so PERIOD_N holds whole cycles).
// Played at TONE_LEVEL, below LEVEL_START. Selected by the Mac with the command 'M', 1.
static constexpr float TONE_HZ = 1000.0f;
static constexpr float TONE_LEVEL = 0.03f;
static_assert(TONE_LEVEL <= LEVEL_START, "tone must not be louder than the sweep");
DMAMEM static float tone_tab[PERIOD_N];
static float *volatile table = sweep;        // what the ISR plays
static volatile uint8_t mode = 0, mode_wanted = 0;  // 0 = sweep, 1 = 1 kHz tone

static volatile float level = 0.0f;          // current level (ISR ramps it)
static volatile float level_target = 0.0f;
static uint32_t sweep_pos = 0;
static volatile uint32_t tx_underruns = 0;

// Pin 21 (BCLK) drive strength / speed and DATA-pin hysteresis. These only affect signal
// quality on the long mic wires; nothing here touches the output level. The Mac can change
// them at runtime with a 4-byte command: 'P', dse (1-7), speed (0-3), hys (0/1).
// flags: bit0 = DATA hysteresis, bit1 = BCLK fast slew, bit2 = DATA keeper OFF,
//        bit3 = sample DATA on the FALLING edge instead of the rising edge.
static uint8_t pad_dse = 6, pad_speed = 2, pad_hys = 1;  // DSE 6 / SPEED 2 = chip default
static uint8_t rx_falling = 0;
static void set_pads(uint8_t dse, uint8_t speed, uint8_t flags);

static const uint32_t SAI_DIV_1M6 = 7;  // 1.6 MHz, standard mode (start-up)
static const uint32_t SAI_DIV_3M2 = 3;  // 3.2 MHz, ultrasonic mode

// Ring buffer filled by the SAI interrupt, drained by loop().
static const uint32_t RING_WORDS = 131072;  // 0.33 s at 2 x 200k words/s (always even)
DMAMEM static uint16_t ring[RING_WORDS];
static volatile uint32_t ring_head = 0;  // written by ISR
static volatile uint32_t ring_tail = 0;  // written by loop
static volatile uint32_t total_words = 0;
static volatile uint32_t dropped_words = 0;
static volatile uint32_t fifo_errors = 0;
static volatile uint16_t bclk_khz = 0;

static const uint16_t PKT_WORDS = 1024;  // 512 per mic
static uint8_t pkt[32 + PKT_WORDS * 2];
static uint32_t seq = 0;

// Same PLL4 setup as the Teensy Audio library (utility/imxrt_hw.cpp, Frank B).
static void set_audio_pll(int nfact, int32_t nmult, uint32_t ndiv) {
  CCM_ANALOG_PLL_AUDIO = CCM_ANALOG_PLL_AUDIO_BYPASS | CCM_ANALOG_PLL_AUDIO_ENABLE |
                         CCM_ANALOG_PLL_AUDIO_POST_DIV_SELECT(2) |
                         CCM_ANALOG_PLL_AUDIO_DIV_SELECT(nfact);
  CCM_ANALOG_PLL_AUDIO_NUM = nmult & CCM_ANALOG_PLL_AUDIO_NUM_MASK;
  CCM_ANALOG_PLL_AUDIO_DENOM = ndiv & CCM_ANALOG_PLL_AUDIO_DENOM_MASK;
  CCM_ANALOG_PLL_AUDIO &= ~CCM_ANALOG_PLL_AUDIO_POWERDOWN;
  while (!(CCM_ANALOG_PLL_AUDIO & CCM_ANALOG_PLL_AUDIO_LOCK)) {
  }
  CCM_ANALOG_MISC2 &= ~(CCM_ANALOG_MISC2_DIV_MSB | CCM_ANALOG_MISC2_DIV_LSB);
  CCM_ANALOG_PLL_AUDIO &= ~CCM_ANALOG_PLL_AUDIO_BYPASS;
}

static void sai_isr() {
  // FRF is set while the FIFO holds more than RFW (15) words, i.e. at least 16.
  while (I2S1_RCSR & I2S_RCSR_FRF) {
    uint32_t head = ring_head;
    for (int i = 0; i < 16; i++) {
      uint16_t w1 = (uint16_t)I2S1_RDR0;  // mic 1
      uint16_t w2 = (uint16_t)I2S1_RDR1;  // mic 2
      // Need 2 free slots; keep one slot empty so head == tail means "empty".
      uint32_t used = (head - ring_tail) & (RING_WORDS - 1);
      if (used >= RING_WORDS - 2) {
        dropped_words++;
      } else {
        ring[head] = w1;
        ring[head + 1] = w2;  // head is always even, so this never wraps
        head = (head + 2) & (RING_WORDS - 1);
      }
    }
    ring_head = head;
    total_words += 16;
  }
  if (I2S1_RCSR & I2S_RCSR_FEF) {  // FIFO overrun
    fifo_errors++;
    I2S1_RCSR |= I2S_RCSR_FEF;     // write 1 to clear
  }
  asm volatile("dsb");
}

static void sai2_tx_isr() {
  // Transmit: FRF is set while the TX FIFO holds 16 words or fewer; add 8 frames (16 words).
  while (I2S2_TCSR & I2S_TCSR_FRF) {
    float lv = level;
    const float tgt = level_target;
    // Ramp rate scales with the target, so any level takes RAMP_UP_S to reach (>= 0.5 s rule).
    const float up = fmaxf(tgt, 0.001f) / (RAMP_UP_S * FS);
    const float down = LEVEL_START / (RAMP_DOWN_S * FS);
    for (int i = 0; i < 8; i++) {
      if (lv < tgt) lv = fminf(lv + up, tgt);
      else if (lv > tgt) lv = fmaxf(lv - down, tgt);
      if (lv > LEVEL_CAP) lv = LEVEL_CAP;
      int32_t s = (int32_t)lrintf(table[sweep_pos] * lv * 32767.0f);
      if (s > SAMPLE_LIMIT) s = SAMPLE_LIMIT;     // final hard clamp
      if (s < -SAMPLE_LIMIT) s = -SAMPLE_LIMIT;
      if (++sweep_pos >= PERIOD_N) sweep_pos = 0;
      I2S2_TDR0 = (uint32_t)(uint16_t)(int16_t)s;  // word 0 = left  = amp A
      I2S2_TDR0 = 0;                               // word 1 = right = amp B: always silent
    }
    level = lv;
  }
  if (I2S2_TCSR & I2S_TCSR_FEF) {  // TX underrun
    tx_underruns++;
    I2S2_TCSR |= I2S_TCSR_FEF;
  }
  asm volatile("dsb");
}

static void sai_set_bclk(uint32_t div) {
  // Stop the receiver and its bit clock (takes effect at the end of the current frame).
  I2S1_RCSR &= ~(I2S_RCSR_RE | I2S_RCSR_BCE);
  while (I2S1_RCSR & (I2S_RCSR_RE | I2S_RCSR_BCE)) {
  }
  // Async master, BCP: sample inputs on the rising edge (mic with SEL = GND drives on falling).
  I2S1_RCR2 = I2S_RCR2_SYNC(0) | (rx_falling ? 0 : I2S_RCR2_BCP) | I2S_RCR2_MSEL(1) | I2S_RCR2_BCD |
              I2S_RCR2_DIV(div);
  I2S1_RCSR = I2S_RCSR_RE | I2S_RCSR_BCE | I2S_RCSR_FRIE | I2S_RCSR_FR;
  bclk_khz = 25600 / (2 * (div + 1));
}

static void sai_begin() {
  CCM_CCGR5 |= CCM_CCGR5_SAI1(CCM_CCGR_ON);

  // PLL4 = 24 MHz * 29.8667 = 716.8 MHz; SAI1 root = 716.8 / 4 / 7 = 25.6 MHz.
  const int fs = 100000, n1 = 4;
  const int n2 = 1 + (24000000 * 27) / (fs * 256 * n1);  // = 7
  const double C = ((double)fs * 256 * n1 * n2) / 24000000;
  const int c0 = C, c2 = 10000;
  const int c1 = C * c2 - (c0 * c2);
  set_audio_pll(c0, c1, c2);

  CCM_CSCMR1 = (CCM_CSCMR1 & ~CCM_CSCMR1_SAI1_CLK_SEL_MASK) | CCM_CSCMR1_SAI1_CLK_SEL(2);
  CCM_CS1CDR = (CCM_CS1CDR & ~(CCM_CS1CDR_SAI1_CLK_PRED_MASK | CCM_CS1CDR_SAI1_CLK_PODF_MASK)) |
               CCM_CS1CDR_SAI1_CLK_PRED(n1 - 1) | CCM_CS1CDR_SAI1_CLK_PODF(n2 - 1);
  IOMUXC_GPR_GPR1 = (IOMUXC_GPR_GPR1 & ~IOMUXC_GPR_GPR1_SAI1_MCLK1_SEL_MASK) |
                    IOMUXC_GPR_GPR1_SAI1_MCLK_DIR | IOMUXC_GPR_GPR1_SAI1_MCLK1_SEL(0);

  I2S1_RCSR = 0;
  I2S1_RMR = 0;                   // receive both words of every frame (continuous bitstream)
  I2S1_RCR1 = I2S_RCR1_RFW(15);   // request when >= 16 words are waiting
  I2S1_RCR3 = I2S_RCR3_RCE_2CH;   // channels 0 and 1 = RX_DATA0 (pin 8), RX_DATA1 (pin 6)
  I2S1_RCR4 = I2S_RCR4_FRSZ(1) | I2S_RCR4_SYWD(15) | I2S_RCR4_MF | I2S_RCR4_FSE |
              I2S_RCR4_FSP | I2S_RCR4_FSD;
  I2S1_RCR5 = I2S_RCR5_WNW(15) | I2S_RCR5_W0W(15) | I2S_RCR5_FBT(15);

  CORE_PIN21_CONFIG = 3;                   // pin 21 = SAI1_RX_BCLK (mic CLK + amp BCLK)
  CORE_PIN8_CONFIG = 3;                    // pin 8 = SAI1_RX_DATA0 (mic 1 DATA)
  CORE_PIN6_CONFIG = 3;                    // pin 6 = SAI1_RX_DATA1 (mic 2 DATA)
  IOMUXC_SAI1_RX_DATA0_SELECT_INPUT = 2;
  IOMUXC_SAI1_RX_DATA1_SELECT_INPUT = 1;

  // Long (~1 m) mic wires: BCLK drive strength is tunable (see set_pads); DATA pins get
  // hysteresis and the keeper.
  set_pads(pad_dse, pad_speed, pad_hys);

  attachInterruptVector(IRQ_SAI1, sai_isr);
  NVIC_SET_PRIORITY(IRQ_SAI1, 64);
  NVIC_ENABLE_IRQ(IRQ_SAI1);
}

static void build_sweep() {
  const float k = (F1 - F0) / (CHIRP_N / FS);  // Hz per second
  double sum = 0, wsum = 0;
  for (uint32_t n = 0; n < PERIOD_N; n++) {
    float v = 0, w = 0;
    if (n < CHIRP_N) {
      const float t = n / FS;
      v = sinf(2.0f * (float)M_PI * (F0 * t + 0.5f * k * t * t));
      w = 1.0f;
      if (n < FADE_N) w = 0.5f - 0.5f * cosf((float)M_PI * n / FADE_N);
      else if (n >= CHIRP_N - FADE_N) w = 0.5f - 0.5f * cosf((float)M_PI * (CHIRP_N - 1 - n) / FADE_N);
    }
    sweep[n] = v * w;
    sum += v * w;
    wsum += w;
  }
  // Zero-mean without un-zeroing the edges: remove the mean in proportion to the window.
  float peak = 0;
  for (uint32_t n = 0; n < CHIRP_N; n++) {
    float w = 1.0f;
    if (n < FADE_N) w = 0.5f - 0.5f * cosf((float)M_PI * n / FADE_N);
    else if (n >= CHIRP_N - FADE_N) w = 0.5f - 0.5f * cosf((float)M_PI * (CHIRP_N - 1 - n) / FADE_N);
    sweep[n] -= (float)(sum / wsum) * w;
    peak = fmaxf(peak, fabsf(sweep[n]));
  }
  for (uint32_t n = 0; n < PERIOD_N; n++) sweep[n] /= peak;
  for (uint32_t n = 0; n < PERIOD_N; n++) tone_tab[n] = sinf(2.0f * (float)M_PI * TONE_HZ * n / FS);
  arm_dcache_flush(sweep, sizeof(sweep));
  arm_dcache_flush(tone_tab, sizeof(tone_tab));
}

// Start the amp transmitter on SAI2 (async master, own pins). Level starts at 0.
static void sai_tx_begin() {
  CCM_CCGR5 |= CCM_CCGR5_SAI2(CCM_CCGR_ON);
  // Same PLL4 (already set by sai_begin) and the same dividers as SAI1: 716.8 / 4 / 7 = 25.6 MHz.
  CCM_CSCMR1 = (CCM_CSCMR1 & ~CCM_CSCMR1_SAI2_CLK_SEL_MASK) | CCM_CSCMR1_SAI2_CLK_SEL(2);
  CCM_CS2CDR = (CCM_CS2CDR & ~(CCM_CS2CDR_SAI2_CLK_PRED_MASK | CCM_CS2CDR_SAI2_CLK_PODF_MASK)) |
               CCM_CS2CDR_SAI2_CLK_PRED(4 - 1) | CCM_CS2CDR_SAI2_CLK_PODF(7 - 1);
  IOMUXC_GPR_GPR1 = (IOMUXC_GPR_GPR1 & ~IOMUXC_GPR_GPR1_SAI2_MCLK3_SEL_MASK) |
                    IOMUXC_GPR_GPR1_SAI2_MCLK_DIR | IOMUXC_GPR_GPR1_SAI2_MCLK3_SEL(0);

  I2S2_TCSR = 0;
  I2S2_TMR = 0;
  I2S2_TCR1 = I2S_TCR1_RFW(16);   // request while <= 16 words in FIFO
  I2S2_TCR2 = I2S_TCR2_SYNC(0) | I2S_TCR2_BCP | I2S_TCR2_BCD | I2S_TCR2_DIV(SAI_DIV_3M2) |
              I2S_TCR2_MSEL(1);   // 3.2 MHz BCLK, data changes on the falling edge
  I2S2_TCR3 = I2S_TCR3_TCE;       // channel 0 = TX_DATA = pin 2
  I2S2_TCR4 = I2S_TCR4_FRSZ(1) | I2S_TCR4_SYWD(15) | I2S_TCR4_MF | I2S_TCR4_FSE |
              I2S_TCR4_FSP | I2S_TCR4_FSD;   // standard I2S: LRCLK low = left = amp A
  I2S2_TCR5 = I2S_TCR5_WNW(15) | I2S_TCR5_W0W(15) | I2S_TCR5_FBT(15);
  I2S2_TCSR = I2S_TCSR_FR;
  for (int i = 0; i < 32; i++) I2S2_TDR0 = 0;  // pre-fill with silence

  attachInterruptVector(IRQ_SAI2, sai2_tx_isr);
  NVIC_SET_PRIORITY(IRQ_SAI2, 64);
  NVIC_ENABLE_IRQ(IRQ_SAI2);

  CORE_PIN2_CONFIG = 2;    // pin 2 = SAI2_TX_DATA -> both amps' DIN
  CORE_PIN3_CONFIG = 2;    // pin 3 = SAI2_TX_SYNC (LRCLK) -> both amps' LRC
  CORE_PIN4_CONFIG = 2;    // pin 4 = SAI2_TX_BCLK -> both amps' BCLK
  I2S2_TCSR = I2S_TCSR_TE | I2S_TCSR_BCE | I2S_TCSR_FRIE;
}

static void sai_rx_edge(uint8_t falling);

static void set_pads(uint8_t dse, uint8_t speed, uint8_t flags) {
  pad_dse = dse & 7;
  pad_speed = speed & 3;
  pad_hys = flags & 0x0F;
  CORE_PIN21_PADCONFIG = IOMUXC_PAD_DSE(pad_dse) | IOMUXC_PAD_SPEED(pad_speed) |
                         ((flags & 2) ? IOMUXC_PAD_SRE : 0);
  const uint32_t in = ((flags & 4) ? 0 : IOMUXC_PAD_PKE) | ((flags & 1) ? IOMUXC_PAD_HYS : 0);
  CORE_PIN8_PADCONFIG = in;
  CORE_PIN6_PADCONFIG = in;
  uint8_t f = (flags >> 3) & 1;
  if (f != rx_falling) sai_rx_edge(f);
}

// Change only which BCLK edge the mic receiver samples on. The mic clock pauses ~one frame.
static void sai_rx_edge(uint8_t falling) {
  rx_falling = falling;
  if (!(I2S1_RCSR & I2S_RCSR_RE)) return;  // not running yet; sai_set_bclk applies it
  uint32_t csr = I2S1_RCSR & (I2S_RCSR_RE | I2S_RCSR_BCE | I2S_RCSR_FRIE);
  I2S1_RCSR &= ~(I2S_RCSR_RE | I2S_RCSR_BCE);
  while (I2S1_RCSR & (I2S_RCSR_RE | I2S_RCSR_BCE)) {
  }
  if (falling) I2S1_RCR2 &= ~I2S_RCR2_BCP;
  else I2S1_RCR2 |= I2S_RCR2_BCP;
  I2S1_RCSR = csr;
}

static void put_u32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static void put_u16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }

void setup() {
  // Amps: no LRCLK, no data -> silent until sai_tx_begin().
  pinMode(PIN_AMP_BCLK, OUTPUT);
  digitalWrite(PIN_AMP_BCLK, LOW);
  pinMode(PIN_AMP_LRCLK, OUTPUT);
  digitalWrite(PIN_AMP_LRCLK, LOW);
  pinMode(PIN_AMP_DIN, OUTPUT);
  digitalWrite(PIN_AMP_DIN, LOW);
  pinMode(LED_BUILTIN, OUTPUT);

  Serial.begin(0);  // USB serial; baud rate is ignored

  build_sweep();
  sai_begin();
  sai_set_bclk(SAI_DIV_1M6);  // standard mode first
  delay(200);                 // mic power-up/wake <= 50 ms; wait well past it
  sai_set_bclk(SAI_DIV_3M2);  // now ultrasonic mode
  delay(20);                  // mic mode change <= 10 ms
  sai_tx_begin();             // amps get clocks now; level is 0 until the Mac opens the port
}

void loop() {
  static bool was_open = false;
  static bool was_open_prev = false;
  static uint32_t last_led = 0;

  if (millis() - last_led >= 250) {  // heartbeat: LED blinks 2x per second
    last_led = millis();
    digitalToggle(LED_BUILTIN);
  }

  // Commands from the Mac: 'P' dse speed flags (pin settings), 'M' m 0 0 (0 sweep, 1 tone).
  while (Serial.available() >= 4) {
    int c = Serial.read();
    if (c != 'P' && c != 'M') continue;
    uint8_t d = Serial.read(), sp = Serial.read(), h = Serial.read();
    if (c == 'P' && d >= 1 && d <= 7 && sp <= 3 && h <= 15) set_pads(d, sp, h);
    if (c == 'M' && d <= 1) mode_wanted = d;
  }
  // Mode change: fade to silence, swap the table, then the normal soft start brings it back.
  if (mode_wanted != mode && level == 0.0f) {
    table = mode_wanted ? tone_tab : sweep;
    mode = mode_wanted;
  }

  bool open = Serial.dtr();
  if (open && !was_open) {  // Mac just opened the port: start from fresh data
    noInterrupts();
    ring_tail = ring_head;
    dropped_words = 0;
    interrupts();
    seq = 0;
  }
  was_open = open;
  float tgt = mode ? TONE_LEVEL : LEVEL_START;
  if (mode_wanted != mode) tgt = 0.0f;       // fading out for a mode change
  level_target = open ? tgt : 0.0f;          // sound only while the Mac has the port open
  if (!open && was_open_prev) mode_wanted = 0;  // port just closed: back to the sweep
  was_open_prev = open;
  if (!open) {
    ring_tail = ring_head;  // nobody listening: discard
    return;
  }

  uint32_t avail = (ring_head - ring_tail) & (RING_WORDS - 1);
  if (avail < PKT_WORDS) return;

  noInterrupts();
  uint32_t tw = total_words, dw = dropped_words, fe = fifo_errors;
  interrupts();

  memcpy(pkt, "SNR1", 4);
  put_u32(pkt + 4, seq++);
  put_u32(pkt + 8, micros());
  put_u32(pkt + 12, tw);
  put_u32(pkt + 16, dw);
  put_u32(pkt + 20, fe);
  put_u16(pkt + 24, PKT_WORDS);
  put_u16(pkt + 26, bclk_khz);
  memset(pkt + 28, 0, 4);
  pkt[28] = 2;  // n_mics
  pkt[29] = pad_dse | (pad_speed << 3) | ((pad_hys & 1) << 5) | (rx_falling << 6);
  put_u16(pkt + 30, (uint16_t)lrintf(level * 1000.0f));  // tenths of a percent
  uint32_t t = ring_tail;
  for (uint16_t i = 0; i < PKT_WORDS; i++) {
    put_u16(pkt + 32 + 2 * i, ring[t]);
    t = (t + 1) & (RING_WORDS - 1);
  }
  ring_tail = t;
  Serial.write(pkt, sizeof(pkt));
}
