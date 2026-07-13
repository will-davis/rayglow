#include "i2s_mic.h"
#include "dsp.h"        // SAMPLE_RATE, CHUNK
#include "config.h"     // I2S pin map
#include "freertos/FreeRTOS.h"   // portMAX_DELAY
#include "driver/i2s_std.h"

static i2s_chan_handle_t rx = nullptr;

// Ring buffer of normalized float samples. Power-of-two size so wrap is a mask.
// Must exceed the largest window (SUB_WINDOW=2048) plus one CHUNK of headroom.
#define RING 4096
static float ring[RING];
static int   head = 0;               // next write index

static int32_t raw[CHUNK];           // one frame of raw 32-bit I2S words

void mic_init() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  i2s_new_channel(&chan_cfg, nullptr, &rx);   // RX only

  i2s_std_config_t std_cfg = {
    .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                    I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = (gpio_num_t)I2S_BCLK_PIN,
      .ws   = (gpio_num_t)I2S_WS_PIN,
      .dout = I2S_GPIO_UNUSED,
      .din  = (gpio_num_t)I2S_DIN_PIN,
      .invert_flags = {},
    },
  };
  // INMP441 with L/R tied to GND drives data in the LEFT time slot.
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

  i2s_channel_init_std_mode(rx, &std_cfg);
  i2s_channel_enable(rx);
}

void mic_read_chunk() {
  size_t got = 0;
  i2s_channel_read(rx, raw, sizeof(raw), &got, portMAX_DELAY);
  int n = (int)(got / sizeof(int32_t));
  for (int i = 0; i < n; ++i) {
    // INMP441 emits 24-bit data left-justified in the 32-bit slot; the low 8
    // bits are zero. Arithmetic-shift down 8 to get a sign-extended 24-bit
    // sample, then normalize to ~[-1, 1). Absolute scale is irrelevant -
    // AutoGain normalizes it away downstream.
    int32_t s = raw[i] >> 8;
    ring[head] = (float)s / 8388608.0f;   // 2^23
    head = (head + 1) & (RING - 1);
  }
}

void mic_latest(float* out, int count) {
  int idx = (head - count) & (RING - 1);
  for (int i = 0; i < count; ++i) {
    out[i] = ring[idx];
    idx = (idx + 1) & (RING - 1);
  }
}
