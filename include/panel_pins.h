// ESP32 GPIO -> HUB75 signal, as wired (female jumpers into the panel's JIN).
// Shared by main.cpp and panel_test.cpp so they can't drift apart.
// Note: this panel's silkscreen labels the D pin "GND" - it's really D.
#pragma once

#define P_R1 25
#define P_G1 26
#define P_B1 27
#define P_R2 14
#define P_G2 12
#define P_B2 13
#define P_A  23
#define P_B  19
#define P_C   5
#define P_D  17   // TX2 on the board
#define P_E  18
#define P_LAT 4
#define P_OE 15
#define P_CLK 16  // RX2 on the board
