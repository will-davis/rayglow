#include "dsp.h"
#include "fft.h"
#include <math.h>

// Precomputed tables (built once in dsp_init) ---------------------------------
static float ENVELOPE[WINDOW];        // Hann over 576 samples (sender.py:128)
static float SUB_ENVELOPE[SUB_WINDOW];// Hann over 2048 samples (sender.py:136)
static float EQUALIZE[SPEC_BINS];     // log equalize table    (sender.py:130)

// Band edges: SPEC_BINS*i/6 for i=0..3 -> 0, 85, 170, 256 (sender.py:132).
static const int BAND_EDGES[4] = {0, 85, 170, 256};

void dsp_init() {
  for (int i = 0; i < WINDOW; ++i)
    ENVELOPE[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * i / WINDOW);
  for (int i = 0; i < SUB_WINDOW; ++i)
    SUB_ENVELOPE[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * i / SUB_WINDOW);
  // EQUALIZE[i] = -0.02 * ln((512 - i) / 512); EQUALIZE[0] = 0 (bin 0 -> DC).
  for (int i = 0; i < SPEC_BINS; ++i)
    EQUALIZE[i] = -0.02f * logf((float)(SPEC_BINS - i) / (float)SPEC_BINS);
}

Bands dsp_analyze(const float* w576) {
  static float windowed[WINDOW];
  for (int i = 0; i < WINDOW; ++i) windowed[i] = w576[i] * ENVELOPE[i];

  static float mag[SPEC_BINS];
  fft_mag(windowed, NFREQ, WINDOW, mag, SPEC_BINS);   // 576 samples, 1024-pt
  for (int k = 0; k < SPEC_BINS; ++k) mag[k] *= EQUALIZE[k];

  float b[3] = {0.0f, 0.0f, 0.0f};
  for (int band = 0; band < 3; ++band)
    for (int k = BAND_EDGES[band]; k < BAND_EDGES[band + 1]; ++k)
      b[band] += mag[k];

  Bands out;
  out.bass = b[0]; out.mid = b[1]; out.treb = b[2];
  out.vol = b[0] + b[1] + b[2];
  return out;
}

float dsp_analyze_sub(const float* w2048) {
  static float windowed[SUB_WINDOW];
  for (int i = 0; i < SUB_WINDOW; ++i) windowed[i] = w2048[i] * SUB_ENVELOPE[i];

  static float mag[6];
  fft_mag(windowed, SUB_WINDOW, SUB_WINDOW, mag, 6);  // bins 0..5
  float s = 0.0f;
  for (int k = 1; k < 6; ++k) s += mag[k];            // bins 1..5 = 23-117 Hz
  return s;
}

static inline float adjust_rate_to_fps(float rate, float fps) {
  return powf(rate, 30.0f / fps);   // sender.py:345 (30 fps reference)
}

void AutoGain::update(float imm, float fps, float& imm_rel, float& avg_rel) {
  float rate = (imm > avg) ? 0.2f : 0.5f;     // attack rising / falling
  rate = adjust_rate_to_fps(rate, fps);
  avg = avg * rate + imm * (1.0f - rate);

  rate = (frame < 50) ? 0.9f : 0.992f;        // long avg: fast converge, then slow
  rate = adjust_rate_to_fps(rate, fps);
  long_avg = long_avg * rate + imm * (1.0f - rate);
  frame++;

  if (fabsf(long_avg) < 0.001f) { imm_rel = 1.0f; avg_rel = 1.0f; return; }
  imm_rel = imm / long_avg;
  avg_rel = avg / long_avg;
}
