#pragma once
#include <stdint.h>
#include <stddef.h>

// Network transport seam. Exactly one backend is compiled in, selected at build
// time:
//   default             -> net_udp.cpp     (WiFi STA + WiFiUDP + ArduinoOTA)
//   -D TRANSPORT_ESPNOW  -> net_espnow.cpp  (pure ESP-NOW to the dongle; no WiFi/OTA)
// main.cpp is transport-agnostic: it computes packets and calls transport_send();
// success/failure accounting lives in the backend (UDP: endPacket result;
// ESP-NOW: the async send-status callback = actual delivery ACKs).

void        transport_init();     // bring the link up (once, in setup)
void        transport_loop();     // per-loop servicing (WiFi reconnect/OTA; nop for ESP-NOW)
bool        transport_ready();    // link usable right now? (ESP-NOW: always true)
void        transport_send(const uint8_t* data, size_t len);

uint32_t    transport_ok();       // successful sends since last reset
uint32_t    transport_fail();     // failed sends since last reset
void        transport_reset_stats();
const char* transport_name();     // short label for the debug line
