#include "vinyl.h"
#include <math.h>

// ---- look. tweak these on the real panel ----
// vinyl shades are "how bright it looks" (0-255). VINYL_GAMMA pushes them darker on top
// of the panel library's own correction, so the record reads as black, not grey.
// the cover skips it and only gets the library's correction, so its colours stay true
#define VINYL_GAMMA   2.2f
#define VINYL_BASE   40.0f    // vinyl grey
#define GROOVE_DEPTH  8.0f    // how strong the fine grooves are
#define TRACK_GAP    14.0f    // how much darker the rings between tracks are
#define SHINE       140.0f    // brightness of the fixed light reflection
#define SHINE_ANGLE  -0.785f  // where the light sits, radians (-45deg = top right)
#define STREAK       60.0f    // brightness of the light streaks that turn with the record
#define STREAK_WIDTH 16       // higher = thinner streaks
#define RPM           8.0f    // a real LP is 33.3, way too fast on 64 pixels
#define SPIN_RATE     2.5f    // how fast it spins up/down. higher = snappier
#define SHARPEN       0.6f    // crisper text on the label. 0 = off, ~1 = strong
#define FRAME_MS     33       // ~30fps

static const float CX = 31.5f, CY = 31.5f;   // panel centre, between the middle 4 pixels
static const float R_DISC  = 31.6f;
static const float R_LABEL = 20.5f;          // label = 41px across
#define LABEL_PX 42                          // cover is shrunk to this
static const float TWO_PI_F = 6.2831853f;

static MatrixPanel_I2S_DMA *panel = nullptr;
static SemaphoreHandle_t lock;               // only one thing draws on the panel at a time
static bool active = false;                  // record mode on/off
static bool needsBackground = false;
static volatile bool playing = false;
static uint8_t label[LABEL_PX][LABEL_PX][3]; // cover shrunk to label size, RGB888
static float angle = 0, speed = 0;
static uint8_t gammaLut[256];

// the label stays upright (rotated text is unreadable at this size), so the spin is shown
// by streaks sweeping around the grooves. everything that doesn't move is worked out once
static uint8_t vinylBase[64][64];            // fixed vinyl shade per pixel, 0 = not vinyl
static uint8_t vinylEdge[64][64];            // outer edge fade, 255 = solid
static uint8_t pixAngle[64][64];             // angle around the centre, 0-255
static uint8_t streakLut[256];

static inline uint8_t clamp8(float v) { return v < 0 ? 0 : v > 255 ? 255 : (uint8_t)v; }

// vinyl is grey, so one value. ignores the label
static float vinylShade(float dx, float dy, float r) {
  float v = VINYL_BASE;
  if (r < R_LABEL + 2.0f) {
    v += 6.0f;                                       // smooth "dead wax" by the label
  } else {
    v += GROOVE_DEPTH * sinf(r * 2.4f);              // fine grooves
    const float gaps[] = {25.5f, 28.5f};             // darker rings between tracks
    for (float g : gaps) if (fabsf(r - g) < 0.6f) v -= TRACK_GAP;
  }
  // the reflection stays put while the record turns, like a light on a real one
  float s = cosf(2.0f * (atan2f(dy, dx) - SHINE_ANGLE));
  if (s > 0) v += SHINE * powf(s, 8) * (0.4f + 0.6f * (r - R_LABEL) / (R_DISC - R_LABEL));
  if (r > R_DISC - 1.2f) v += 20.0f;                 // slightly bright outer rim
  return v;
}

static inline float edgeFade(float r) {              // soft outer edge
  float e = R_DISC + 0.5f - r;
  return e >= 1.0f ? 1.0f : e <= 0 ? 0 : e;
}

static void precompute() {
  for (int i = 0; i < 256; i++) {
    gammaLut[i] = (uint8_t)(powf(i / 255.0f, VINYL_GAMMA) * 255.0f + 0.5f);
    float s = cosf(2.0f * i / 256.0f * TWO_PI_F);   // two streaks, opposite each other
    streakLut[i] = s > 0 ? clamp8(STREAK * powf(s, STREAK_WIDTH)) : 0;
  }
  for (int y = 0; y < 64; y++) {
    for (int x = 0; x < 64; x++) {
      float dx = x - CX, dy = y - CY, r = sqrtf(dx * dx + dy * dy);
      bool vinyl = r >= R_LABEL + 0.5f && r < R_DISC + 0.5f;
      vinylBase[y][x] = vinyl ? max((uint8_t)1, clamp8(vinylShade(dx, dy, r) * edgeFade(r))) : 0;
      vinylEdge[y][x] = clamp8(edgeFade(r) * 255.0f);
      float a = atan2f(dy, dx);
      pixAngle[y][x] = (uint8_t)((int)(a / TWO_PI_F * 256.0f + 256.0f) & 255);
    }
  }
}

// bilinear sample of the upright label
static void labelColor(float dx, float dy, float out[3]) {
  const float k = (LABEL_PX / 2) / R_LABEL, mid = LABEL_PX / 2 - 0.5f, last = LABEL_PX - 1;
  float u = dx * k + mid, v = dy * k + mid;
  u = u < 0 ? 0 : u > last ? last : u;
  v = v < 0 ? 0 : v > last ? last : v;
  int x0 = (int)u, y0 = (int)v;
  int x1 = x0 < last ? x0 + 1 : x0, y1 = y0 < last ? y0 + 1 : y0;
  float fx = u - x0, fy = v - y0;
  for (int i = 0; i < 3; i++) {
    float top = label[y0][x0][i] + (label[y0][x1][i] - label[y0][x0][i]) * fx;
    float bot = label[y1][x0][i] + (label[y1][x1][i] - label[y1][x0][i]) * fx;
    out[i] = top + (bot - top) * fy;
  }
}

// label + its soft edge + spindle hole. only redrawn when the cover changes
static void drawLabel() {
  const int lo = (int)(CX - R_LABEL - 1), hi = (int)(CX + R_LABEL + 2);
  for (int y = lo; y <= hi; y++) {
    for (int x = lo; x <= hi; x++) {
      float dx = x - CX, dy = y - CY, r = sqrtf(dx * dx + dy * dy);
      if (r >= R_LABEL + 0.5f) continue;
      float col[3] = {0, 0, 0};                      // spindle hole stays black
      if (r >= 1.2f) {
        labelColor(dx, dy, col);
        float t = R_LABEL + 0.5f - r;
        if (t < 1.0f) {                              // blend into the vinyl at the edge
          float vin = gammaLut[clamp8(vinylShade(dx, dy, r))];
          for (int i = 0; i < 3; i++) col[i] = col[i] * t + vin * (1 - t);
        }
      }
      panel->drawPixelRGB888(x, y, clamp8(col[0]), clamp8(col[1]), clamp8(col[2]));
    }
  }
}

// the vinyl ring, with the streaks at the record's current angle
static void drawVinyl(uint8_t turn) {
  for (int y = 0; y < 64; y++) {
    for (int x = 0; x < 64; x++) {
      if (!vinylBase[y][x] && !vinylEdge[y][x]) { panel->drawPixelRGB888(x, y, 0, 0, 0); continue; }
      if (!vinylBase[y][x]) continue;                // inside the label
      int v = vinylBase[y][x] + streakLut[(uint8_t)(pixAngle[y][x] - turn)] * vinylEdge[y][x] / 255;
      uint8_t g = gammaLut[v > 255 ? 255 : v];
      panel->drawPixelRGB888(x, y, g, g, g);
    }
  }
}

// little signature in the top-right corner, the one spot the record doesn't reach
static const char *HEART[] = {".#.#.", "#####", "#####", ".###.", "..#.."};
static const char *LETTER_Z[] = {"####", "..#.", ".#..", "#...", "####"};

static void drawPattern(const char *rows[], int x0, int y0, uint8_t r, uint8_t g, uint8_t b) {
  for (int y = 0; y < 5; y++)
    for (int x = 0; rows[y][x]; x++)
      if (rows[y][x] == '#') panel->drawPixelRGB888(x0 + x, y0 + y, r, g, b);
}

static void drawSignature() {
  drawPattern(HEART, 53, 1, 200, 40, 60);            // soft pink-red
  drawPattern(LETTER_Z, 59, 1, 140, 110, 100);       // dim warm white
}

static void renderTask(void *) {
  TickType_t lastWake = xTaskGetTickCount();
  uint32_t prevMs = millis();
  int drawnTurn = -1;
  for (;;) {
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(FRAME_MS));
    uint32_t now = millis();
    float dt = (now - prevMs) / 1000.0f;
    prevMs = now;

    // ease toward full speed or a stop, like a turntable motor
    float target = playing ? RPM / 60.0f * TWO_PI_F : 0;
    speed += (target - speed) * fminf(1.0f, dt * SPIN_RATE);
    if (fabsf(target - speed) < 0.01f) speed = target;
    angle = fmodf(angle + speed * dt, TWO_PI_F);
    uint8_t turn = (uint8_t)(angle / TWO_PI_F * 256.0f);   // clockwise, y points down

    xSemaphoreTake(lock, portMAX_DELAY);
    if (active) {
      if (needsBackground) { drawLabel(); drawnTurn = -1; needsBackground = false; }
      if (turn != drawnTurn) {                       // stopped = no redraw
        drawVinyl(turn);                             // also blanks the corners, so...
        drawSignature();                             // ...put the signature back on top
        drawnTurn = turn;
      }
    }
    xSemaphoreGive(lock);
  }
}

void vinylBegin(MatrixPanel_I2S_DMA *p) {
  panel = p;
  precompute();
  lock = xSemaphoreCreateMutex();
  // core 0: the main loop (TLS, JSON, JPEG) runs on core 1, so polling can't stall the spin
  xTaskCreatePinnedToCore(renderTask, "vinyl", 4096, nullptr, 1, nullptr, 0);
}

// cover arrives from the JPEG decoder in blocks. each source pixel is added to the label
// pixel it lands in, so a 300px cover becomes a proper average, not a blurry resample
static uint32_t (*acc)[4] = nullptr;         // r, g, b, count per label pixel
static int coverW, coverH;

bool vinylCoverStart(int w, int h) {
  acc = (uint32_t (*)[4])calloc(LABEL_PX * LABEL_PX, sizeof(*acc));
  coverW = w; coverH = h;
  return acc != nullptr;
}

void vinylCoverBlock(int x, int y, int w, int h, const uint16_t *rgb565) {
  if (!acc) return;
  for (int j = 0; j < h; j++) {
    int sy = y + j;
    if (sy < 0 || sy >= coverH) continue;
    uint32_t *row = acc[sy * LABEL_PX / coverH * LABEL_PX];
    for (int i = 0; i < w; i++) {
      int sx = x + i;
      if (sx < 0 || sx >= coverW) continue;
      uint16_t p = rgb565[j * w + i];
      uint32_t *a = row + (sx * LABEL_PX / coverW) * 4;
      a[0] += ((p >> 11) & 0x1F) * 255 / 31;
      a[1] += ((p >> 5) & 0x3F) * 255 / 63;
      a[2] += (p & 0x1F) * 255 / 31;
      a[3]++;
    }
  }
}

void vinylCoverDone() {
  if (!acc) return;
  static uint8_t avg[LABEL_PX][LABEL_PX][3], next[LABEL_PX][LABEL_PX][3];
  for (int y = 0; y < LABEL_PX; y++)
    for (int x = 0; x < LABEL_PX; x++) {
      uint32_t *a = acc[y * LABEL_PX + x];
      for (int i = 0; i < 3; i++) avg[y][x][i] = a[3] ? a[i] / a[3] : 0;
    }
  free(acc);
  acc = nullptr;

  // unsharp mask: push each pixel away from its neighbours' average, so text edges hold up
  for (int y = 0; y < LABEL_PX; y++)
    for (int x = 0; x < LABEL_PX; x++)
      for (int i = 0; i < 3; i++) {
        int sum = 0, n = 0;
        for (int j = -1; j <= 1; j++)
          for (int k = -1; k <= 1; k++) {
            int yy = y + j, xx = x + k;
            if (yy >= 0 && yy < LABEL_PX && xx >= 0 && xx < LABEL_PX) { sum += avg[yy][xx][i]; n++; }
          }
        float c = avg[y][x][i];
        next[y][x][i] = clamp8(c + SHARPEN * (c - (float)sum / n));
      }

  xSemaphoreTake(lock, portMAX_DELAY);
  memcpy(label, next, sizeof(label));
  needsBackground = true;                            // new label (and full redraw)
  active = true;
  xSemaphoreGive(lock);
}

void vinylSetPlaying(bool p) { playing = p; }

void vinylStop() {
  xSemaphoreTake(lock, portMAX_DELAY);               // waits out a frame mid-draw
  active = false;
  xSemaphoreGive(lock);
}
