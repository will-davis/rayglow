// RayGLow ESP32 microphone feature-sender.
//
// Captures an INMP441 I2S mic, runs a faithful port of ../sender.py's band DSP,
// and sends the identical v1 feature packet (564 B) to the Pi. The transport is
// pluggable (transport.h): WiFi/UDP (net_udp.cpp) or ESP-NOW (net_espnow.cpp).
// A drop-in alternative audio source that can be located anywhere.
//
// Run ONE sender at a time per Pi: this and the desktop sender.py share the same
// seq space and would fight (the receiver is latest-wins).

#include <Arduino.h>
#include "esp_timer.h"    // esp_timer_get_time() - microsecond stage timing

#include "dsp.h"
#include "i2s_mic.h"
#include "packet.h"
#include "transport.h"

static FeaturePacketV1 pkt;

// One AutoGain instance per band (bass, mid, treb, vol, sub) - matches sender.py.
static AutoGain agBass, agMid, agTreb, agVol, agSub;
static uint32_t seq = 0;

// Transmit throttle. We COMPUTE every loop (~187 Hz: fresh features, low latency)
// but only SEND at the rate below. The Pi receiver is latest-wins, so sending
// faster than it renders is wasted; on the UDP transport it also floods the
// ~32-deep lwIP TX pool during a WiFi stall (endPacket -> ENOMEM). The 90 Hz gate
// on the 187 Hz loop quantizes to every 3rd frame (~62 Hz effective).
static const int64_t TX_INTERVAL_US = 1000000 / 90;   // -> ~62 Hz effective
static int64_t last_tx_us = 0;

// Scratch windows pulled from the mic ring each frame.
static float w576[WINDOW];
static float w2048[SUB_WINDOW];
static float w128[128];

void setup() {
  Serial.begin(115200);
  // Native USB (HWCDC): make debug writes NON-BLOCKING. Otherwise, when the USB
  // is enumerated but no monitor is draining the port, the CDC TX buffer fills
  // and Serial.printf() blocks forever -> the loop hangs -> ESP-NOW stops. With
  // timeout 0, writes drop when no reader, so the sender runs headless.
  Serial.setTxTimeoutMs(0);
  delay(200);
  Serial.println("\nrayglow-mic booting");

  dsp_init();
  mic_init();
  transport_init();                  // WiFi/UDP or ESP-NOW, per build

  pkt.magic   = PACKET_MAGIC;
  pkt.version = PACKET_VERSION;
  pkt.flags   = 0;                   // source_domain = AUDIO; beat bits unused
}

static uint32_t last_dbg = 0;

void loop() {
  transport_loop();                  // WiFi reconnect/OTA service (nop for ESP-NOW)

  // Stage timing (us). t_a..t_d bracket the three loop costs: (b-a) I2S wait =
  // buffering latency; (c-b) DSP compute; (d-c) transport handoff (NOT transit).
  int64_t t_a = esp_timer_get_time();
  mic_read_chunk();                  // blocks ~CHUNK/SR s -> paces the loop
  int64_t t_b = esp_timer_get_time();

  mic_latest(w576,  WINDOW);
  mic_latest(w2048, SUB_WINDOW);
  mic_latest(w128,  128);

  Bands b = dsp_analyze(w576);
  float subRaw = dsp_analyze_sub(w2048);

  float bass, mid, treb, vol, sub, bass_att, mid_att, treb_att, vol_att, sub_att;
  agBass.update(b.bass, SEND_FPS, bass, bass_att);
  agMid .update(b.mid,  SEND_FPS, mid,  mid_att);
  agTreb.update(b.treb, SEND_FPS, treb, treb_att);
  agVol .update(b.vol,  SEND_FPS, vol,  vol_att);  // vol_att unused (receiver reuses vol)
  agSub .update(subRaw, SEND_FPS, sub,  sub_att);
  int64_t t_c = esp_timer_get_time();

  pkt.seq = seq++;
  pkt.t   = millis() / 1000.0f;
  pkt.bass = bass; pkt.mid = mid; pkt.treb = treb;
  pkt.bass_att = bass_att; pkt.mid_att = mid_att; pkt.treb_att = treb_att;
  pkt.vol = vol;
  for (int i = 0; i < 128; ++i) pkt.wave[i] = w128[i];
  pkt.sub = sub; pkt.sub_att = sub_att;

  if (transport_ready() && (t_c - last_tx_us) >= TX_INTERVAL_US) {  // throttled, freshest pkt
    last_tx_us = t_c;
    transport_send((const uint8_t*)&pkt, sizeof(pkt));
  }
  int64_t t_d = esp_timer_get_time();

  // Accumulate per-stage avg/max over the 1 Hz reporting interval.
  static int64_t sum_wait = 0, sum_comp = 0, sum_send = 0;
  static int64_t max_wait = 0, max_comp = 0, max_send = 0;
  static uint32_t frames = 0;
  int64_t d_wait = t_b - t_a, d_comp = t_c - t_b, d_send = t_d - t_c;
  sum_wait += d_wait; sum_comp += d_comp; sum_send += d_send;
  if (d_wait > max_wait) max_wait = d_wait;
  if (d_comp > max_comp) max_comp = d_comp;
  if (d_send > max_send) max_send = d_send;
  frames++;

  uint32_t now = millis();
  if (now - last_dbg >= 1000) {      // 1 Hz status, mirrors sender.py --debug
    last_dbg = now;
    // Raw mic health over the 2048-sample window: normalized floats, so |v|~1
    // means near full-scale. If rms is ~1e-4 or peak never leaves ~0, the mic
    // is silent/miswired or the >>8 shift in i2s_mic.cpp needs adjusting.
    float mn = 1e9f, mx = -1e9f, sumsq = 0.0f;
    for (int i = 0; i < SUB_WINDOW; ++i) {
      float v = w2048[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
      sumsq += v * v;
    }
    float rms = sqrtf(sumsq / SUB_WINDOW);
    Serial.printf("%-8s bass %.2f mid %.2f treb %.2f sub %.2f vol %.2f | "
                  "raw b/m/t %.4f/%.4f/%.4f sub %.4f | "
                  "mic min %+.4f max %+.4f rms %.4f\n",
                  transport_ready() ? transport_name() : "DOWN",
                  bass, mid, treb, sub, vol, b.bass, b.mid, b.treb, subRaw,
                  mn, mx, rms);
    // Timing (ms): avg/max per stage; loop (compute) rate ~187 Hz; tx/fail are
    // the throttled send counts over the last second (fail = stall / no ACK).
    Serial.printf("  timing(ms) wait %.2f/%.2f  dsp %.2f/%.2f  send %.2f/%.2f "
                  " @ %lu Hz | tx %lu fail %lu\n",
                  sum_wait / 1000.0 / frames, max_wait / 1000.0,
                  sum_comp / 1000.0 / frames, max_comp / 1000.0,
                  sum_send / 1000.0 / frames, max_send / 1000.0,
                  (unsigned long)frames,
                  (unsigned long)transport_ok(), (unsigned long)transport_fail());
    sum_wait = sum_comp = sum_send = 0;
    max_wait = max_comp = max_send = 0;
    frames = 0;
    transport_reset_stats();
  }
}
