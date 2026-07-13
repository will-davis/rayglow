// ESP-NOW transport: pure peer-to-peer to the XIAO C3 dongle on a FIXED channel,
// no WiFi association, no OTA (flash the mic over its USB-C). Compiled only when
// the build selects -D TRANSPORT_ESPNOW; otherwise this file is empty and
// net_udp.cpp provides the transport.
#ifdef TRANSPORT_ESPNOW

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <string.h>
#include "config.h"       // DONGLE_MAC, ESPNOW_CHANNEL
#include "transport.h"

static const uint8_t dongle_mac[6] = DONGLE_MAC;
static uint32_t ok_count = 0, fail_count = 0;

// Delivery status is asynchronous: esp_now_send() only queues; this callback
// fires with the MAC-layer ACK result, so ok/fail reflect ACTUAL delivery to the
// dongle (a great "is it reaching the dongle" signal).
static void on_sent(const esp_now_send_info_t* info, esp_now_send_status_t status) {
  (void)info;
  if (status == ESP_NOW_SEND_SUCCESS) ok_count++;
  else                                fail_count++;
}

void transport_init() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();                 // never associate to an AP
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);

  if (esp_now_init() != ESP_OK) { Serial.println("esp_now_init FAILED"); return; }
  esp_now_register_send_cb(on_sent);

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, dongle_mac, 6);
  peer.channel = ESPNOW_CHANNEL;     // 0 would mean "current channel"; be explicit
  peer.ifidx   = WIFI_IF_STA;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  Serial.printf("ESP-NOW up  ch %u -> %02X:%02X:%02X:%02X:%02X:%02X\n",
                ESPNOW_CHANNEL, dongle_mac[0], dongle_mac[1], dongle_mac[2],
                dongle_mac[3], dongle_mac[4], dongle_mac[5]);
}

void transport_loop() { /* connectionless: nothing to service */ }

bool transport_ready() { return true; }   // always ready; no association state

void transport_send(const uint8_t* data, size_t len) {
  if (esp_now_send(dongle_mac, data, len) != ESP_OK) fail_count++;  // queue reject
  // success is counted asynchronously in on_sent()
}

uint32_t transport_ok()   { return ok_count; }
uint32_t transport_fail() { return fail_count; }
void transport_reset_stats() { ok_count = fail_count = 0; }
const char* transport_name() { return "ESP-NOW"; }

#endif  // TRANSPORT_ESPNOW
