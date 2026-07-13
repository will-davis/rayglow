#pragma once
// Copy this file to config.h and fill in real values. config.h is gitignored
// (mirrors the repo's LOCAL-SETUP.md convention) so creds never get committed.

// ---- WiFi (must be a 2.4 GHz network; the ESP32 has no 5 GHz radio) --------
#define WIFI_SSID   "your-2.4GHz-ssid"
#define WIFI_PASS   "your-wifi-password"

// ---- Destination: the Pi renderer running rayglow.render -------------------
#define RAYGLOW_HOST "192.168.0.50"   // Pi IP (same default as sender.py)
#define RAYGLOW_PORT 5005

// ---- OTA (ArduinoOTA) ------------------------------------------------------
#define OTA_HOSTNAME "rayglow-mic"
#define OTA_PASSWORD "rayglow-ota"    // must match --auth passed to espota

// ---- ESP-NOW transport (build env s3zero-espnow) ---------------------------
// The dongle prints its MAC on boot; paste it here. Channel must match the
// dongle's ESPNOW_CHANNEL. (WiFi creds above are unused in the ESP-NOW build.)
#define ESPNOW_CHANNEL 1
#define DONGLE_MAC {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}

// ---- INMP441 I2S pin map ---------------------------------------------------
// Pins are normally set per-board in platformio.ini build_flags:
//   s3zero  -> SD=IO4, SCK=IO5, WS=IO6   (Waveshare ESP32-S3-Zero)
//   esp32dev-> SD=IO32, SCK=IO14, WS=IO15 (ESP32-WROOM-32)
// The #ifndef guards below are only fallbacks if you build without those flags.
// INMP441 wiring: VDD->3V3, GND->GND, L/R->GND (selects the LEFT slot),
//   SCK->I2S_BCLK_PIN, WS->I2S_WS_PIN, SD->I2S_DIN_PIN.
#ifndef I2S_BCLK_PIN
#define I2S_BCLK_PIN 5
#endif
#ifndef I2S_WS_PIN
#define I2S_WS_PIN   6
#endif
#ifndef I2S_DIN_PIN
#define I2S_DIN_PIN  4
#endif
