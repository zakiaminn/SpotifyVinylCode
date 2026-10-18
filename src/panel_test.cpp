// Panel wiring test for the Spotify Vinyl Display.
// Flash this FIRST (target "paneltest"), before the real firmware, to prove the
// 64x64 HUB75 panel is wired to the ESP32 correctly. It ignores WiFi/Spotify.
//
// What you should see on the panel, looping:
//   1. Whole panel RED, then GREEN, then BLUE (2s each)
//   2. A white 1-pixel border plus an X corner to corner
//   3. A green dot sweeping across every pixel, top-left to bottom-right
//
// Reading the result:
//   - A colour comes up wrong (e.g. "RED" step looks blue) -> R/G/B wires swapped.
//   - Only the TOP or only the BOTTOM half lights -> an R2/G2/B2 wire, or an A-E wire.
//   - Image doubled / split / shifted sideways -> set FLIP_CLK to 1 below, reflash.
//   - Panel stays totally black but wiring looks right -> set USE_FM6126A to 1, reflash.

#include <Arduino.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

#include "panel_pins.h"   // wiring: ESP32 GPIO -> HUB75 signal

// ---- troubleshooting toggles (see notes at the top) ----
#define USE_FM6126A 0     // 1 if the panel stays black
#define FLIP_CLK    0     // 1 if the image is shifted or split sideways

MatrixPanel_I2S_DMA *panel = nullptr;

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n== Panel wiring test ==");

  HUB75_I2S_CFG::i2s_pins pins = {
    P_R1, P_G1, P_B1, P_R2, P_G2, P_B2,
    P_A, P_B, P_C, P_D, P_E, P_LAT, P_OE, P_CLK
  };
  HUB75_I2S_CFG cfg(64, 64, 1, pins);   // one 64x64 panel
#if USE_FM6126A
  cfg.driver = HUB75_I2S_CFG::FM6126A;
#endif
#if FLIP_CLK
  cfg.clkphase = false;
#endif

  panel = new MatrixPanel_I2S_DMA(cfg);
  if (!panel->begin()) {
    Serial.println("panel begin() FAILED - check 5V power and the CLK/LAT/OE wiring");
  }
  panel->setBrightness8(70);   // ~27% - easy on the eyes and the power supply
  panel->clearScreen();
}

void loop() {
  panel->fillScreenRGB888(70, 0, 0);  Serial.println("RED");   delay(2000);
  panel->fillScreenRGB888(0, 70, 0);  Serial.println("GREEN"); delay(2000);
  panel->fillScreenRGB888(0, 0, 70);  Serial.println("BLUE");  delay(2000);

  panel->clearScreen();
  for (int i = 0; i < 64; i++) {
    panel->drawPixelRGB888(i, 0, 200, 200, 200);
    panel->drawPixelRGB888(i, 63, 200, 200, 200);
    panel->drawPixelRGB888(0, i, 200, 200, 200);
    panel->drawPixelRGB888(63, i, 200, 200, 200);
    panel->drawPixelRGB888(i, i, 200, 200, 200);
    panel->drawPixelRGB888(63 - i, i, 200, 200, 200);
  }
  Serial.println("BORDER + X"); delay(2500);

  panel->clearScreen();
  for (int y = 0; y < 64; y++) {
    for (int x = 0; x < 64; x++) {
      panel->drawPixelRGB888(x, y, 0, 110, 0);
      delay(1);
    }
  }
  Serial.println("SWEEP done"); delay(800);
}
