#pragma once
#include <stdint.h>

// RayGLow feature packet, v1 (564 bytes). Byte-identical to the v1 layout that
// rayglow/feed/receiver.py accepts: struct "<IHHIf7f128f2f", little-endian.
// The ESP32 is little-endian, so this packed struct maps 1:1 onto the wire.
//
// The receiver dispatches on (version, EXACT byte length) and checks magic
// after unpack, so this MUST stay exactly 564 bytes with version == 1.

#define PACKET_MAGIC   0x4D494C4Bu   // "MILK"
#define PACKET_VERSION 1u

#pragma pack(push, 1)
struct FeaturePacketV1 {
  uint32_t magic;      // PACKET_MAGIC
  uint16_t version;    // 1
  uint16_t flags;      // 0 (source_domain=AUDIO; beat bits unused on v1)
  uint32_t seq;        // strictly increasing, else the receiver drops as stale
  float    t;          // seconds since boot; must advance (drives d/dt, phase)
  float    bass;       // AutoGain ratios: 1.0 = typical, quiet ~0.5, hits ~2-3
  float    mid;
  float    treb;
  float    bass_att;
  float    mid_att;
  float    treb_att;
  float    vol;        // overall; receiver reuses this as its own attenuated val
  float    wave[128];  // mono waveform, ~[-1, 1] (receiver clamps to +/-1)
  float    sub;        // true 23-117 Hz sub band, AutoGain ratio
  float    sub_att;
};
#pragma pack(pop)

static_assert(sizeof(FeaturePacketV1) == 564, "v1 packet must be exactly 564 bytes");
