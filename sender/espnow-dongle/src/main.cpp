// RayGLow ESP-NOW receiver dongle (Seeed XIAO ESP32-C3).
//
// Pure ESP-NOW receiver on a FIXED channel (must match the mic). Each valid v1
// packet is relayed verbatim to the Pi over UART. The receive callback runs in
// the WiFi task, so it only copies the freshest frame into a shared buffer under
// a spinlock; loop() does the (potentially blocking) UART write. Latest-wins: if
// two frames arrive before loop() drains, we forward the newest - which is
// exactly the receiver's own semantics, so dropping intermediate frames is fine.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "packet.h"

// Must match the mic's ESPNOW_CHANNEL. Pick a fixed channel (retune later if the
// band is busy). Both ends are un-associated, so nothing else dictates it.
static const uint8_t  ESPNOW_CHANNEL = 1;

static const uint32_t UART_BAUD = 921600;   // 564 B * ~90 Hz ~= 0.5 Mbit/s; headroom
// XIAO ESP32-C3 UART pins: D6 = GPIO21 (TX -> Pi RX), D7 = GPIO20 (RX, unused).
static const int UART_TX_PIN = 21;   // D6
static const int UART_RX_PIN = 20;   // D7

static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
static uint8_t  rx_buf[sizeof(FeaturePacketV1)];
static volatile bool     rx_ready = false;
static volatile uint32_t rx_count = 0, fwd_count = 0, bad_count = 0;

static void on_recv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  (void)info;
  if (len != (int)sizeof(FeaturePacketV1)) { bad_count++; return; }
  uint32_t magic;
  memcpy(&magic, data, 4);
  if (magic != PACKET_MAGIC) { bad_count++; return; }
  portENTER_CRITICAL(&mux);
  memcpy(rx_buf, data, sizeof(rx_buf));
  rx_ready = true;
  portEXIT_CRITICAL(&mux);
  rx_count++;
}

void setup() {
  Serial.begin(115200);
  // Native USB (HWCDC): non-blocking debug writes so a filled CDC TX buffer (USB
  // enumerated but no monitor draining it) can't hang loop() and stall relaying.
  // Harmless when the USB-C is unplugged in the case; matters if you ever plug in
  // to debug and leave the port closed.
  Serial.setTxTimeoutMs(0);
  delay(200);
  Serial.println("\nrayglow espnow-dongle booting");

  // UART to the Pi. Bigger TX buffer than one packet so write() never blocks.
  Serial1.setTxBufferSize(1024);
  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);

  // Pure ESP-NOW: STA mode but never associate; pin the channel.
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);

  if (esp_now_init() != ESP_OK) {
    Serial.println("esp_now_init FAILED - halting");
    while (true) delay(1000);
  }
  esp_now_register_recv_cb(on_recv);

  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  Serial.printf("esp-now ready  ch %u  uart %lu 8N1 on GPIO%d(TX)\n",
                ESPNOW_CHANNEL, (unsigned long)UART_BAUD, UART_TX_PIN);
  Serial.printf("DONGLE MAC: %02X:%02X:%02X:%02X:%02X:%02X\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  Serial.printf("  paste into mic config.h -> "
                "#define DONGLE_MAC {0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X}\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

static uint32_t last_dbg = 0;

void loop() {
  if (rx_ready) {
    static uint8_t out[sizeof(FeaturePacketV1)];
    portENTER_CRITICAL(&mux);
    memcpy(out, rx_buf, sizeof(out));
    rx_ready = false;
    portEXIT_CRITICAL(&mux);
    Serial1.write(out, sizeof(out));   // relay verbatim to the Pi
    fwd_count++;
  }

  uint32_t now = millis();
  if (now - last_dbg >= 1000) {         // 1 Hz health line over USB
    last_dbg = now;
    Serial.printf("rx %lu  fwd %lu  bad %lu  /s\n",
                  (unsigned long)rx_count, (unsigned long)fwd_count,
                  (unsigned long)bad_count);
    rx_count = fwd_count = bad_count = 0;
  }
}
