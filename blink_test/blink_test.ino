// Upload sanity check for the Teensy 4.0: blinks the onboard LED (pin 13)
// 1 s on, 1 s off.
// It drives no other pins.

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(1000);
  digitalWrite(LED_BUILTIN, LOW);
  delay(1000);
}
