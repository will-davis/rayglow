#pragma once
#include <stdint.h>

// Faithful C++ port of ../sender.py's band analysis. Constants MUST match the
// desktop sender exactly, because render/textures.py's texel calibration is
// tuned to these band semantics ("1.0 = typical", AutoGain-normalized).

static const int   SAMPLE_RATE = 48000;   // sender.py:122

static const int WINDOW    = 576;         // band FFT input samples (sender.py:123)
static const int NFREQ     = 1024;        // zero-padded transform size (:124)
static const int SPEC_BINS = 512;         // NFREQ/2 magnitude bins (:125)
static const int SUB_WINDOW = 2048;       // sub-band window (sender.py:135)

// Capture/send cadence. One blocking I2S read of CHUNK samples per loop; in
// steady state the loop period is max(compute, CHUNK/SR). CHUNK must satisfy
// CHUNK/SR >= compute or the loop drops below real time and the DMA buffer
// backlogs (pinning latency at the DMA depth). With the ESP-DSP SIMD FFT,
// measured compute is ~3.1 ms/loop (down from ~5.9 ms scalar), so the floor is
// CHUNK >= ~150. 256 -> 5.33 ms/frame (187 Hz) keeps ~2 ms headroom against
// per-frame spikes while keeping the packet rate modest for a busy 2.4 GHz net.
// SEND_FPS feeds AutoGain's fps-corrected decay so normalization is unchanged
// in wall-clock terms. History: 800->16.7ms(60Hz) scalar; 384->8ms(125Hz)
// scalar; 256->5.33ms(187Hz) with SIMD FFT.
static const int   CHUNK    = 256;
static const float SEND_FPS = (float)SAMPLE_RATE / (float)CHUNK;   // 187.5 Hz

void dsp_init();   // precompute envelope + equalize tables

struct Bands { float bass, mid, treb, vol; };

// 576 samples (oldest->newest) -> raw band energies (pre-normalization).
Bands dsp_analyze(const float* w576);

// 2048 samples -> raw sub-bass energy (23-117 Hz, no equalize).
float dsp_analyze_sub(const float* w2048);

// MilkDrop per-band normalizer (sender.py AutoGain, plugin.cpp:8750). Each band
// gets its own instance. Divides by a long running average so imm_rel hovers
// ~1.0. imm_rel -> the packet band value; avg_rel -> the *_att value.
struct AutoGain {
  float avg = 0.0f;
  float long_avg = 0.0f;
  uint32_t frame = 0;
  void update(float imm, float fps, float& imm_rel, float& avg_rel);
};
