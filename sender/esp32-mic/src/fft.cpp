#include "fft.h"
#include <math.h>

// Largest window we transform is the 2048-pt sub-band FFT.
static const int FFT_MAX = 2048;

#ifdef USE_ESP_DSP
// ---------------------------------------------------------------------------
// ESP-DSP backend (ESP32-S3): hardware-accelerated radix-2 FFT. The precompiled
// libespressif__esp-dsp.a ships inside framework-arduinoespressif32-libs, and
// the public dsps_fft2r_fc32 macro dispatches to the S3's SIMD (aes3) path. We
// keep fft_mag()'s signature identical, so callers are unchanged.
// ---------------------------------------------------------------------------
#include "dsps_fft2r.h"

// Interleaved complex work buffer [re0,im0,re1,im1,...]; 16-byte aligned for SIMD.
static __attribute__((aligned(16))) float cbuf[2 * FFT_MAX];
static bool inited = false;

void fft_mag(const float* in, int n, int in_len, float* mag_out, int nbins) {
  if (n > FFT_MAX) n = FFT_MAX;
  if (!inited) { dsps_fft2r_init_fc32(nullptr, FFT_MAX); inited = true; }

  for (int i = 0; i < n; ++i) {
    cbuf[2 * i]     = (i < in_len) ? in[i] : 0.0f;   // real
    cbuf[2 * i + 1] = 0.0f;                          // imag (real input)
  }
  dsps_fft2r_fc32(cbuf, n);        // in-place radix-2, SIMD on S3
  dsps_bit_rev_fc32(cbuf, n);      // reorder butterfly output to natural bins
  for (int k = 0; k < nbins; ++k) {
    float re = cbuf[2 * k], im = cbuf[2 * k + 1];
    mag_out[k] = sqrtf(re * re + im * im);
  }
}

#else
// ---------------------------------------------------------------------------
// Portable scalar backend (WROOM / any target): self-contained radix-2 DFT.
// ---------------------------------------------------------------------------
static float re[FFT_MAX];
static float im[FFT_MAX];

// Iterative in-place radix-2 Cooley-Tukey DFT over re[]/im[], length n (a power
// of two). Standard decimation-in-time with a bit-reversal permutation first.
static void fft_run(int n) {
  for (int i = 1, j = 0; i < n; ++i) {
    int bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      float tr = re[i]; re[i] = re[j]; re[j] = tr;
      float ti = im[i]; im[i] = im[j]; im[j] = ti;
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    float ang = -2.0f * (float)M_PI / (float)len;
    float wlr = cosf(ang), wli = sinf(ang);
    for (int i = 0; i < n; i += len) {
      float wr = 1.0f, wi = 0.0f;
      for (int k = 0; k < len / 2; ++k) {
        int a = i + k, b = a + len / 2;
        float xr = re[b] * wr - im[b] * wi;
        float xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr; im[b] = im[a] - xi;
        re[a] += xr;        im[a] += xi;
        float nwr = wr * wlr - wi * wli;
        wi = wr * wli + wi * wlr;
        wr = nwr;
      }
    }
  }
}

void fft_mag(const float* in, int n, int in_len, float* mag_out, int nbins) {
  if (n > FFT_MAX) n = FFT_MAX;
  for (int i = 0; i < n; ++i) {
    re[i] = (i < in_len) ? in[i] : 0.0f;
    im[i] = 0.0f;
  }
  fft_run(n);
  for (int k = 0; k < nbins; ++k) {
    mag_out[k] = sqrtf(re[k] * re[k] + im[k] * im[k]);
  }
}
#endif
