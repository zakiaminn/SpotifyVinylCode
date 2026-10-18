// Spinning record on the 64x64 panel: black vinyl with grooves and a fixed shine,
// album cover as the round centre label. Runs in its own task so Spotify polling
// never makes it stutter.
#pragma once
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

void vinylBegin(MatrixPanel_I2S_DMA *panel);   // call once after the panel is up
// feed a decoded cover in: start with its size, pass every JPEG block, then done.
// done swaps the new cover onto the label and shows the record
bool vinylCoverStart(int w, int h);
void vinylCoverBlock(int x, int y, int w, int h, const uint16_t *rgb565);
void vinylCoverDone();
void vinylSetPlaying(bool playing);            // spins up / spins down
void vinylStop();                              // hand the panel back (messages, blank)
