#pragma once

// INMP441 I2S MEMS microphone capture. Uses the IDF5 `driver/i2s_std.h` API
// (stable and identical on the WROOM and the S3). Maintains a ring buffer of
// recent normalized samples so the DSP can pull its various window lengths.

void mic_init();

// Block-read one 60 Hz frame (CHUNK samples) from I2S into the ring. Because
// the read blocks until the audio hardware has delivered CHUNK samples, calling
// this once per loop paces the whole send loop at exactly FPS (the audio clock
// is the timebase). Returns after ~1/FPS seconds.
void mic_read_chunk();

// Copy the `count` freshest samples into out[], chronological (oldest->newest),
// matching sender.py's Capture.latest(n).
void mic_latest(float* out, int count);
