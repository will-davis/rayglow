#pragma once

// Real-input FFT magnitude spectrum.
//
//   in       : in_len real samples (already windowed by the caller)
//   n        : transform size, a power of two, in_len <= n <= FFT_MAX (2048).
//              samples [in_len, n) are treated as zero (zero-padding).
//   mag_out  : receives nbins magnitudes  sqrt(re^2 + im^2)  (bins 0..nbins-1).
//
// This is the portability seam. The WROOM's FPU makes this self-contained
// radix-2 transform plenty fast for the v1 band FFTs (~sub-millisecond at
// 2048 pt). On the S3 (or for a v2 4096-pt spectrum) swap the body for
// ESP-DSP's dsps_fft2r_fc32 without touching any caller.
void fft_mag(const float* in, int n, int in_len, float* mag_out, int nbins);
