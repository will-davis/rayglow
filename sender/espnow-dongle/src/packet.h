#pragma once
#include <stdint.h>

// RayGLow feature packet, v1 (564 bytes). Byte-identical to the layout that
// rayglow/feed/receiver.py accepts and that sender/esp32-mic emits: struct
// "<IHHIf7f128f2f", little-endian. Copied verbatim from
// sender/esp32-mic/src/packet.h so the dongle can validate frames it relays.
//
// The dongle does not interpret the body - it only checks magic + length before
// forwarding - but keeping the full struct documents the contract and lets the
// static_assert catch any drift.

#define PACKET_MAGIC   0x4D494C4Bu   // "MILK"
#define PACKET_VERSION 1u

#pragma pack(push, 1)
struct FeaturePacketV1 {
  uint32_t magic;      // PACKET_MAGIC
  uint16_t version;    // 1
  uint16_t flags;      // 0
  uint32_t seq;        // strictly increasing
  float    t;          // seconds since boot
  float    bass;
  float    mid;
  float    treb;
  float    bass_att;
  float    mid_att;
  float    treb_att;
  float    vol;
  float    wave[128];
  float    sub;
  float    sub_att;
};
#pragma pack(pop)

static_assert(sizeof(FeaturePacketV1) == 564, "v1 packet must be exactly 564 bytes");
