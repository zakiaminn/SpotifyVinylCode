#include <Arduino.h>
#include <WiFi.h>
#include <WiFiManager.h>          // tzapu/WiFiManager
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <TJpg_Decoder.h>
#include <Preferences.h>
#include "secrets.h"
#include "panel_pins.h"

#define LED_PIN 2
#define BOOT_BTN 0                // onboard BOOT button (GPIO0)
#define AP_NAME  "Vinyl-Setup"    // the hotspot name she'll see on her phone

#define PANEL_SIZE 64
#define BRIGHTNESS 60             // 0-255. capped: looks better + less power draw
#define MAX_ART_BYTES 60000       // Spotify's 64px art is ~2-5KB, this is a safety cap

// ---- token state ----
// The refresh token lives in flash, not in code: it's set from the setup portal and
// Spotify may hand back a new one on every refresh (PKCE), which we must save.
Preferences   prefs;
String        refreshToken;
bool          spotifyRejected = false; // Spotify refused the token -> wait for a relink
String        accessToken;
unsigned long tokenExpiresAt = 0;
unsigned long lastPoll       = 0;
const unsigned long POLL_EVERY_MS = 5000;

// ---- display state ----
MatrixPanel_I2S_DMA *panel = nullptr;
String pendingArtUrl;             // art the last poll asked for
String shownArtUrl;               // art currently on the panel

void startPanel() {
  HUB75_I2S_CFG::i2s_pins pins = {
    P_R1, P_G1, P_B1, P_R2, P_G2, P_B2,
    P_A, P_B, P_C, P_D, P_E, P_LAT, P_OE, P_CLK
  };
  HUB75_I2S_CFG cfg(PANEL_SIZE, PANEL_SIZE, 1, pins);
  panel = new MatrixPanel_I2S_DMA(cfg);
  if (!panel->begin()) Serial.println("panel: begin() FAILED - out of memory?");
  panel->setBrightness8(BRIGHTNESS);
  panel->clearScreen();
}

// up to 3 short centred lines of text. 64px fits 10 characters per line
void showMessage(const char *l1, const char *l2 = "", const char *l3 = "") {
  panel->clearScreen();
  panel->setTextSize(1);
  panel->setTextWrap(false);
  panel->setTextColor(panel->color565(150, 150, 150));
  const char *lines[] = {l1, l2, l3};
  for (int i = 0; i < 3; i++) {
    panel->setCursor((PANEL_SIZE - (int)strlen(lines[i]) * 6) / 2, 18 + i * 10);
    panel->print(lines[i]);
  }
  shownArtUrl = "";               // art needs redrawing after this
}

// TJpg_Decoder hands us the image in small blocks
bool drawJpgBlock(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t *bitmap) {
  panel->drawRGBBitmap(x, y, bitmap, w, h);
  return true;
}

// download a JPEG into a malloc'd buffer. caller frees it
uint8_t *downloadArt(const String &url, size_t &len) {
  WiFiClientSecure client; client.setInsecure();
  HTTPClient https;
  if (!https.begin(client, url)) return nullptr;
  int code = https.GET();
  int size = https.getSize();
  if (code != 200 || size <= 0 || size > MAX_ART_BYTES) {
    Serial.printf("art: HTTP %d, size %d - skipping\n", code, size);
    https.end();
    return nullptr;
  }
  uint8_t *buf = (uint8_t *)malloc(size);
  if (!buf) { Serial.println("art: out of memory"); https.end(); return nullptr; }

  WiFiClient *stream = https.getStreamPtr();
  size_t got = 0;
  unsigned long lastData = millis();
  while (got < (size_t)size && millis() - lastData < 5000) {
    size_t n = stream->available();
    if (n) {
      got += stream->readBytes(buf + got, min(n, (size_t)size - got));
      lastData = millis();
    } else {
      delay(1);
    }
  }
  https.end();
  if (got < (size_t)size) { Serial.println("art: download cut short"); free(buf); return nullptr; }
  len = got;
  return buf;
}

void showArt(const String &url) {
  size_t len = 0;
  uint8_t *jpg = downloadArt(url, len);
  if (!jpg) return;               // shownArtUrl unchanged, so we retry next poll

  uint16_t w = 0, h = 0;
  TJpgDec.getJpgSize(&w, &h, jpg, len);
  uint8_t scale = 1;              // shrink bigger images to fit (decoder does 1/2/4/8)
  while (w / scale > PANEL_SIZE && scale < 8) scale *= 2;
  TJpgDec.setJpgScale(scale);

  panel->clearScreen();
  TJpgDec.drawJpg((PANEL_SIZE - w / scale) / 2, (PANEL_SIZE - h / scale) / 2, jpg, len);
  free(jpg);
  shownArtUrl = url;
  Serial.printf("art: drew %dx%d (1/%d), free heap %u\n", w, h, scale, ESP.getFreeHeap());
}

void saveRefreshToken(const String &token) {
  prefs.putString("refresh", token);
  refreshToken = token;
}

// setup portal: WiFi page + a "Setup" page with the Spotify link code field
WiFiManagerParameter linkHint("<p>Paste the code from the Link Spotify page. "
                              "Leave it blank to keep the current one.</p>");
WiFiManagerParameter linkCode("spotify", "Spotify link code", "", 300);

void onPortalSave() {
  String code = linkCode.getValue();
  code.trim();
  if (code.length() < 20) return;   // blank or junk, keep what we have
  saveRefreshToken(code);
  spotifyRejected = false;
  accessToken = "";                 // use the new one straight away
  Serial.println("portal: new Spotify link code saved.");
}

void configurePortal(WiFiManager &wm) {
  static std::vector<const char *> menu = {"wifi", "param", "exit"};
  wm.setConfigPortalTimeout(180);   // if nobody configures in 3 min, carry on / reboot
  wm.setParamsPage(true);           // link code gets its own page, no WiFi re-entry needed
  wm.addParameter(&linkHint);
  wm.addParameter(&linkCode);
  wm.setSaveParamsCallback(onPortalSave);
  wm.setMenu(menu);
}

// wifi manager
void startWiFi() {
  WiFiManager wm;
  configurePortal(wm);
  wm.setAPCallback([](WiFiManager *) { showMessage("Join WiFi", "Vinyl-", "Setup"); });
  showMessage("Connecting");
  Serial.printf("Connecting to saved WiFi... if it can't, join \"%s\" on your phone.\n", AP_NAME);
  if (!wm.autoConnect(AP_NAME)) {
    Serial.println("WiFi setup timed out — restarting.");
    delay(1000);
    ESP.restart();
  }
  WiFi.setAutoReconnect(true);
  Serial.printf("WiFi connected. IP: %s\n", WiFi.localIP().toString().c_str());
}

//hold boot for 3 sec
void checkForPortalRequest() {
  static unsigned long pressedSince = 0;
  if (digitalRead(BOOT_BTN) == LOW) {                 // button pressed (active-low)
    if (pressedSince == 0) pressedSince = millis();
    else if (millis() - pressedSince > 3000) {
      Serial.printf("\nBOOT held 3s → opening setup portal \"%s\".\n", AP_NAME);
      Serial.println("Join that WiFi network on your phone to reconfigure.");
      WiFiManager wm;
      configurePortal(wm);
      showMessage("Join WiFi", "Vinyl-", "Setup");
      wm.startConfigPortal(AP_NAME);                  // blocks until done/timeout
      Serial.println("Portal closed — resuming.");
      panel->clearScreen();                           // art redraws on the next poll
      accessToken = "";                               // re-auth after any change
      pressedSince = 0;
    }
  } else {
    pressedSince = 0;                                 // released before 3s
  }
}

bool refreshAccessToken() {
  WiFiClientSecure client; client.setInsecure();
  HTTPClient https;
  if (!https.begin(client, "https://accounts.spotify.com/api/token")) return false;
  https.addHeader("Content-Type", "application/x-www-form-urlencoded");
  // PKCE refresh: client id only, no client secret on the device
  int code = https.POST("grant_type=refresh_token&refresh_token=" + refreshToken +
                        "&client_id=" + String(SPOTIFY_CLIENT_ID));
  String payload = https.getString();
  https.end();
  if (code == 400 && payload.indexOf("invalid_grant") >= 0) {
    // revoked, password changed, or an old token. retrying won't help
    Serial.println("token: Spotify rejected the link code. Relink via the setup portal.");
    spotifyRejected = true;
    showMessage("Spotify", "needs", "relink");
    return false;
  }
  if (code != 200) { Serial.printf("token: HTTP %d — %s\n", code, payload.c_str()); return false; }
  JsonDocument doc;
  if (deserializeJson(doc, payload)) return false;
  accessToken = doc["access_token"].as<String>();
  int expiresIn = doc["expires_in"] | 3600;
  tokenExpiresAt = millis() + (unsigned long)(expiresIn - 60) * 1000UL;
  String rotated = doc["refresh_token"] | "";
  if (rotated.length() && rotated != refreshToken) {
    saveRefreshToken(rotated);      // the old one may stop working, so save right away
    Serial.println("token: Spotify rotated the refresh token, saved.");
  }
  Serial.printf("token: refreshed, valid ~%ds\n", expiresIn);
  return true;
}

void pollNowPlaying() {
  WiFiClientSecure client; client.setInsecure();
  HTTPClient https;
  if (!https.begin(client, "https://api.spotify.com/v1/me/player/currently-playing")) return;
  https.addHeader("Authorization", "Bearer " + accessToken);
  int code = https.GET();

  if (code == 200) {
    String payload = https.getString();
    JsonDocument filter;
    filter["is_playing"] = true;
    filter["item"]["name"] = true;
    filter["item"]["artists"][0]["name"] = true;
    filter["item"]["album"]["name"] = true;
    filter["item"]["album"]["images"][0]["url"] = true;   // [0] = applies to every image
    filter["item"]["album"]["images"][0]["height"] = true;
    JsonDocument doc;
    if (deserializeJson(doc, payload, DeserializationOption::Filter(filter))) { https.end(); return; }

    const char *track  = doc["item"]["name"]  | "(unknown)";
    const char *artist = doc["item"]["artists"][0]["name"] | "(unknown)";
    const char *album  = doc["item"]["album"]["name"] | "(unknown)";
    bool playing       = doc["is_playing"] | false;

    // Spotify gives 640/300/64px versions - pick the smallest
    const char *art = "";
    int smallest = INT_MAX;
    for (JsonObject img : doc["item"]["album"]["images"].as<JsonArray>()) {
      int h = img["height"] | INT_MAX - 1;
      if (h < smallest) { smallest = h; art = img["url"] | ""; }
    }
    pendingArtUrl = art;
    digitalWrite(LED_PIN, playing ? HIGH : LOW);
    Serial.println("╭──────── NOW PLAYING ────────");
    Serial.printf ("│ %s\n│ by %s\n│ album: %s  [%s]\n│ art: %s\n",
                   track, artist, album, playing ? "playing" : "paused", art);
    Serial.println("╰─────────────────────────────");
  } else if (code == 204) {
    digitalWrite(LED_PIN, LOW);
    Serial.println("Nothing playing — open Spotify and press play.");
    pendingArtUrl = "";
  } else if (code == 401) {
    accessToken = "";             // force a refresh next loop
  } else {
    Serial.printf("nowplaying: HTTP %d\n", code);
  }
  https.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BOOT_BTN, INPUT_PULLUP);
  delay(1000);
  Serial.println("\n=== Spotify Vinyl — display build ===");
  startPanel();                   // before WiFi, so its DMA buffers get memory first
  TJpgDec.setCallback(drawJpgBlock);
  prefs.begin("spotify", false);
  refreshToken = prefs.getString("refresh", "");
  Serial.printf("spotify: %s\n", refreshToken.length() ? "link code loaded from flash" : "not linked yet");
  Serial.printf("panel: up, free heap %u\n", ESP.getFreeHeap());
  startWiFi();
}

void loop() {
  checkForPortalRequest();                                     // hold BOOT 3s to reconfigure
  if (WiFi.status() != WL_CONNECTED) { delay(500); return; }   // auto-reconnect handles it
  if (refreshToken == "" || spotifyRejected) {                 // nothing to do until relinked
    static bool told = false;
    if (!told && refreshToken == "") {
      Serial.println("spotify: not linked. Hold BOOT 3s, join Vinyl-Setup, paste the code under Setup.");
      showMessage("Spotify", "not", "linked");
      told = true;
    }
    delay(50);
    return;
  }
  if (accessToken == "" || millis() > tokenExpiresAt) {
    if (!refreshAccessToken()) { delay(5000); return; }
  }
  if (millis() - lastPoll >= POLL_EVERY_MS) {
    lastPoll = millis();
    pollNowPlaying();
    // fetch art after the poll's connection is closed, so only one TLS session is open
    if (pendingArtUrl != shownArtUrl) {
      if (pendingArtUrl == "") { panel->clearScreen(); shownArtUrl = ""; }
      else showArt(pendingArtUrl);
    }
  }
}
