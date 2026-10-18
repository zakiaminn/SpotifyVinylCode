# Spotify Vinyl Display

A little display that shows whatever's playing on Spotify. The plan is album art on a
64x64 LED matrix made to look like a spinning record, in a 3D printed case. Building it
as a gift, so it has to be easy to set up from her side once it's shipped.

This repo is the ESP32 firmware, the "Link Spotify" web page, the enclosure files, plus
notes on how it all fits together and every command I actually used, so I don't have to
relearn this next time.

Right now it connects to WiFi through a setup portal, pulls the now-playing track from
Spotify, and draws the album cover on the LED panel.

## Hardware

- ESP32-WROOM-32 dev board, 38 pin, USB-C. The USB-serial chip on it is a CP2102.
- On my Mac it shows up as `/dev/cu.usbserial-0001`. macOS already had the driver, so
  nothing to install there.
- P3 64x64 HUB75 RGB LED panel (192x192mm).
- 5V 6-8A barrel power brick, into a barrel-to-screw-terminal adapter. The panel's power
  cable goes straight to that, never through the ESP32.
- 15 female-to-female jumpers from the panel's input socket (JIN) to the ESP32 pins. No
  breadboard, the board is too wide for one anyway.

### Panel wiring

Pin map lives in `include/panel_pins.h`. With the USB port on the left, the colour wires
all go on the top row and the control wires on the bottom row.

| Panel | ESP32 | Panel | ESP32 |
|---|---|---|---|
| R1 | D25 | A | D23 |
| G1 | D26 | B | D19 |
| B1 | D27 | C | D5 |
| R2 | D14 | D | TX2 (GPIO 17) |
| G2 | D12 | E | D18 |
| B2 | D13 | CLK | RX2 (GPIO 16) |
| GND | GND | LAT | D4 |
| | | OE | D15 |

## The stack

- VS Code with the PlatformIO extension. PlatformIO is what handles the ESP32 compiler,
  flashing the board, and pulling in libraries. Way cleaner than the Arduino IDE for this.
- PlatformIO's command line tool isn't on my PATH, so I call it with the full path:
  `~/.platformio/penv/bin/pio`. If I'd rather click buttons, the same actions are in the
  VS Code bottom bar (checkmark = build, arrow = upload, plug icon = serial monitor).
- Libraries: WiFiManager (setup portal), ArduinoJson, ESP32-HUB75-MatrixPanel-I2S-DMA
  (drives the panel), Adafruit GFX, TJpg_Decoder (decodes the album art JPEG).
- One thing to remember: the ESP32 only does 2.4GHz WiFi. Not 5GHz.

## What's in here

```
platformio.ini            board, port, libraries, and the two build targets
src/main.cpp              the real firmware (target "esp32dev")
src/panel_test.cpp        panel wiring test, colours + border + pixel sweep (target "paneltest")
include/panel_pins.h      which ESP32 pin goes to which panel pin
include/secrets.h         spotify client id. gitignored
include/secrets.example.h blank template of secrets.h (safe to commit)
docs/index.html           the "Link Spotify" page, hosted on GitHub Pages
enclosure/                3D printed case: generator script, STLs, preview renders
```

## Commands I use

Check the board is actually connected. When I plug the ESP32 in, a new
`/dev/cu.usbserial-0001` shows up in this list. If nothing new shows up it's almost always
a charge-only USB cable, so swap for a data cable.

```
ls /dev/cu.*
```

Build (compile without flashing):

```
~/.platformio/penv/bin/pio run -d /Users/zak/Projects/SpotifyVinylCode -e esp32dev
```

Flash it onto the board:

```
~/.platformio/penv/bin/pio run -d /Users/zak/Projects/SpotifyVinylCode -e esp32dev -t upload
```

Flash the panel wiring test instead (swap back to `esp32dev` after):

```
~/.platformio/penv/bin/pio run -d /Users/zak/Projects/SpotifyVinylCode -e paneltest -t upload
```

Watch what the board is printing over serial (115200 baud):

```
~/.platformio/penv/bin/pio device monitor -p /dev/cu.usbserial-0001 -b 115200
```

Close the serial monitor before flashing, otherwise the port's busy and the upload fails.

## How the Spotify login works

This is the part that confused me, so writing it down:

- The Client ID only identifies the app. It does NOT decide whose music shows up.
- The refresh token is what's tied to a specific account. And a refresh token is created
  for whoever is logged into Spotify at the moment they click "Agree" in the browser.
- It uses PKCE, so there's no Client Secret anywhere. Not in the page, not on the board.
  The Client ID being public is fine, that's how PKCE is meant to work.
- To link an account: open https://zakiaminn.github.io/SpotifyVinylCode/, tap Link
  Spotify, Agree. The page shows "Linked as <name>" and a link code (the refresh token).
- To get that code onto the board: hold BOOT for 3 seconds, join the "Vinyl-Setup" WiFi
  on a phone, tap Setup, paste the code into "Spotify link code", Save, Exit.
- The board saves the code in flash. Spotify can hand back a new one each time the board
  refreshes, and the board saves that too. So the code only lives in one place: the board.
  Don't reuse an old copy of it somewhere else.
- If the panel says "Spotify needs relink" (she revoked access or changed her password),
  just do the link steps again. No reflashing.
- In the Spotify dashboard the redirect URI has to be exactly
  `https://zakiaminn.github.io/SpotifyVinylCode/`, and while the app is in development
  mode her account email has to be added under User Management or Spotify blocks her.

## What I built, in order

1. Blink test. Flashed a blinking LED + serial print just to prove Mac to cable to board
   all works before touching anything real.
2. WiFi. Connect to my network and print the IP.
3. Spotify. Refresh-token auth, poll now-playing every 5s, print it over serial. First
   real proof the whole backend works.
4. WiFi setup portal. Ripped out the hardcoded WiFi and put in WiFiManager. Now if the
   board can't connect (like when it's at a new place) it makes its own hotspot called
   "Vinyl-Setup". You join it from a phone, pick your network, done. No reflashing. To
   reopen the portal later, hold the BOOT button for 3 seconds while it's running.
5. Spotify link page + PKCE. Dropped the Client Secret, the refresh token moved from the
   code into flash, and it's set through the portal using the code from the link page.
6. LED panel. Wired it, proved the wiring with the panel test, then the main firmware
   started drawing Spotify's 64px album cover. The panel also shows "Connecting",
   "Join WiFi Vinyl-Setup", and "Spotify not linked" so she's never staring at a black square.

## Stuff that tripped me up

Notes so future me doesn't waste time on the same things:

- Use a data USB cable, not a charge-only one. That's the number one reason the Mac won't
  see the board.
- The ESP32 is 2.4GHz WiFi only. No 5GHz networks.
- On boot the serial monitor spits out a bunch of garbage and lines like
  `ets ... / try 0x400805e4`. That's normal. It's the boot ROM printing at a different
  baud rate. It is not a crash. I panicked over it once and the board was totally fine.
- Do NOT hold the BOOT button while resetting or powering on the board. That drops the
  chip into firmware-download mode instead of running the app, which is why the setup
  portal didn't open the first time I tried. Hold BOOT for 3 seconds while it's already
  running instead.
- The panel has two sockets. Wires go into JIN (the arrows on the back point away from it),
  not JOUT. Data into JOUT goes nowhere.
- On my panel the D pin is printed as "GND" on the board. It isn't ground (checked with a
  multimeter, no beep). It's D, and a 64x64 panel needs it.
- TX2 and RX2 on the ESP32 are GPIO 17 and 16. The board labels them by their serial
  name, not the number, so they're easy to mix up.
- The ESP32 is too wide for a normal breadboard. It either covers every hole or the pins
  land in the power rail, which shorts them all together. Jumpers straight onto the pins.

## Secrets

The only thing in `include/secrets.h` now is the Spotify Client ID, and it's gitignored
anyway. WiFi comes from the portal and the Spotify link code is stored on the board. To
set it up on a fresh checkout, copy `secrets.example.h` to `secrets.h` and fill in the
Client ID.

## What's left

- Spinning vinyl look around the album art.
- Solder the jumpers so nothing rattles loose in shipping.
- Print the enclosure and hide the back so all she deals with is power and WiFi.
