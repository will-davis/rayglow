// Default transport: WiFi STA + WiFiUDP + ArduinoOTA. Compiled unless the build
// selects ESP-NOW (-D TRANSPORT_ESPNOW), in which case this file is empty and
// net_espnow.cpp provides the transport instead.
#ifndef TRANSPORT_ESPNOW

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoOTA.h>
#include "config.h"
#include "transport.h"

static WiFiUDP udp;
static bool wifi_up = false;
static bool ota_started = false;
static uint32_t ok_count = 0, fail_count = 0;

void transport_init() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);              // keep the radio hot for steady UDP
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);  // non-blocking: association runs in the bg
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  // ArduinoOTA.begin() is deferred to the first connect (needs mDNS/net).
}

void transport_loop() {
  bool now_up = (WiFi.status() == WL_CONNECTED);
  if (now_up && !wifi_up) {          // just (re)connected
    WiFi.setSleep(false);            // reassert PS_NONE; it can revert on assoc
    Serial.printf("WiFi up: ip=%s -> pi %s:%d\n",
                  WiFi.localIP().toString().c_str(), RAYGLOW_HOST, RAYGLOW_PORT);
    if (!ota_started) { ArduinoOTA.begin(); ota_started = true;
                        Serial.println("OTA ready"); }
  }
  wifi_up = now_up;
  if (wifi_up) ArduinoOTA.handle();
}

bool transport_ready() { return wifi_up; }

void transport_send(const uint8_t* data, size_t len) {
  udp.beginPacket(RAYGLOW_HOST, RAYGLOW_PORT);
  udp.write(data, len);
  if (udp.endPacket()) ok_count++;   // 1 = ok; 0 = stall/pool-full -> drop, no retry
  else                 fail_count++;
}

uint32_t transport_ok()   { return ok_count; }
uint32_t transport_fail() { return fail_count; }
void transport_reset_stats() { ok_count = fail_count = 0; }
const char* transport_name() { return "WiFi"; }

#endif  // !TRANSPORT_ESPNOW
