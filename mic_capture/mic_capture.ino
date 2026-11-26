// Experiment 1: microphones only.
//
// Clocks both mics (SPH0641LU4H-1, SEL = GND) from Teensy pin 21 and reads their PDM bitstreams
// with the SAI1 receiver: mic 1 DATA on pin 8 (RX_DATA0), mic 2 DATA on pin 6 (RX_DATA1).
// Streams the raw bits to the Mac over USB.
//
// SAFETY: this sketch produces NO audio. The SAI transmitter is never enabled. Pin 7 (amp DIN)
// and pin 20 (amp LRCLK) are held LOW as plain GPIO, so the amps never get a valid I2S stream
// and stay silent. Keep the speakers disconnected anyway for this experiment.
//
// Mic start-up rule (datasheet: "Do not power up into ultrasonic mode"):
//   1. start the clock at 1.6 MHz (standard mode), wait 200 ms,
//   2. then switch to 3.2 MHz (ultrasonic mode, spec 3.072-4.8 MHz).
//
// Clocking: SAI1 root = 25.6 MHz (PLL4 716.8 MHz / 4 / 7).
//   BCLK = 25.6 MHz / (2 * (DIV + 1)): DIV 7 -> 1.6 MHz, DIV 3 -> 3.2 MHz.
//   Frames are 2 x 16-bit words, so at 3.2 MHz each mic gives 200,000 words/s (3.2 Mbit/s).
//
// USB packet (little-endian), sent only while the Mac has the port open:
//   "SNR1" | seq u32 | micros u32 | total_words u32 | dropped_words u32 | fifo_errors u32
//   | n_words u16 | bclk_khz u16 | n_mics u8 | 3 reserved
//   | n_words x u16, interleaved mic1, mic2, mic1, mic2 ... (raw PDM bits, MSB = first bit)
//   total_words and dropped_words count words per mic.

#include <Arduino.h>

static const uint8_t PIN_AMP_LRCLK = 20;
static const uint8_t PIN_AMP_DIN = 7;

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

static void sai_set_bclk(uint32_t div) {
  // Stop the receiver and its bit clock (takes effect at the end of the current frame).
  I2S1_RCSR &= ~(I2S_RCSR_RE | I2S_RCSR_BCE);
  while (I2S1_RCSR & (I2S_RCSR_RE | I2S_RCSR_BCE)) {
  }
  // Async master, BCP: sample inputs on the rising edge (mic with SEL = GND drives on falling).
  I2S1_RCR2 = I2S_RCR2_SYNC(0) | I2S_RCR2_BCP | I2S_RCR2_MSEL(1) | I2S_RCR2_BCD |
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

  attachInterruptVector(IRQ_SAI1, sai_isr);
  NVIC_SET_PRIORITY(IRQ_SAI1, 64);
  NVIC_ENABLE_IRQ(IRQ_SAI1);
}

static void put_u32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static void put_u16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }

void setup() {
  // Amps: no LRCLK, no data -> silent.
  pinMode(PIN_AMP_LRCLK, OUTPUT);
  digitalWrite(PIN_AMP_LRCLK, LOW);
  pinMode(PIN_AMP_DIN, OUTPUT);
  digitalWrite(PIN_AMP_DIN, LOW);
  pinMode(LED_BUILTIN, OUTPUT);

  Serial.begin(0);  // USB serial; baud rate is ignored

  sai_begin();
  sai_set_bclk(SAI_DIV_1M6);  // standard mode first
  delay(200);                 // mic power-up/wake <= 50 ms; wait well past it
  sai_set_bclk(SAI_DIV_3M2);  // now ultrasonic mode
}

void loop() {
  static bool was_open = false;
  static uint32_t last_led = 0;

  if (millis() - last_led >= 250) {  // heartbeat: LED blinks 2x per second
    last_led = millis();
    digitalToggle(LED_BUILTIN);
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
  uint32_t t = ring_tail;
  for (uint16_t i = 0; i < PKT_WORDS; i++) {
    put_u16(pkt + 32 + 2 * i, ring[t]);
    t = (t + 1) & (RING_WORDS - 1);
  }
  ring_tail = t;
  Serial.write(pkt, sizeof(pkt));
}
