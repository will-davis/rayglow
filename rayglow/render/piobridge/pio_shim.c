// pio_shim.c — a thin flat-ABI shim over the Raspberry Pi 5 RP1 piolib.
//
// Why a shim at all: nearly the whole piolib API (sm_config_*, pio_gpio_init,
// pio_sm_init, pio_encode_*, the DMA xfer helpers) is `static inline` in the
// headers — there are no exported symbols for them, so a pure ctypes binding
// can't call them. This file compiles those calls into four real exported
// functions that `rayglow/render/pio_out.py` loads with ctypes.
//
// What it does: drives a 4-lane source-synchronous parallel bus to the RP2350
// (firmware bin `phase6-parallel`): `out pins, 4` presents one nibble across 4
// GPIO while a 1-bit sideset toggles a data clock the RP2350 samples on; two
// nibbles = one byte. DMA feeds the bytes (zero CPU during the burst). CS framing
// + READY handshake are driven from Python (gpiozero) exactly as the SPI path.
// (4 lanes, not 8: the board exposes GP0–27 and the scan-out engine owns GP0–18,
// leaving only GP19–27 for the link — see phase6_parallel.rs.)
//
// Build on the Pi (needs piolib built + installed as a shared lib, see Makefile):
//   cc -shared -fPIC -O2 pio_shim.c -lpio -I<piolib>/include -o libpioshim.so
//
// Lane/bit convention (must match firmware/src/bin/phase6_parallel.rs): this SM
// shifts RIGHT (out pins,4, autopull 32) — low nibble first, lane i = nibble bit
// i, bytes in memory order. The RP2350 RX shifts LEFT (byte lands in ISR[7:0] for
// the byte DMA), so its first-sampled nibble is the HIGH one; `pio_out.py` does a
// per-byte nibble swap before calling send() so the framebuffer comes out
// byte-identical. Validate with a 0x00,0x01,... ramp on a logic analyzer (if every
// byte's nibbles are swapped, toggle PioOut.nibble_swap; if bits within a nibble
// mirror, reverse the lane wiring).

#include <stdint.h>
#include <stdlib.h>

#include "piolib.h"

#define NUM_LANES 4

struct pioshim {
    PIO pio;
    int sm;
    unsigned offset;
    unsigned frame_bytes;
};

// Open the bus: claim a state machine on PIO0, load the 2-instruction TX
// program, map the 8 data lanes (contiguous from data0_pin) + the clock pin, and
// set up the DMA path. Returns NULL on any failure. `clkdiv` divides the 200 MHz
// RP1 clk_sys; the per-lane bit rate is ~clk_sys/(2*clkdiv) (2 SM cycles/byte).
struct pioshim *pioshim_open(unsigned data0_pin, unsigned clk_pin,
                             unsigned frame_bytes, float clkdiv) {
    PIO pio = pio0;                 // == pio_open_helper(0)
    if (!pio)
        return NULL;
    pio_select(pio);               // sm_config_* helpers target pio_get_current()

    int sm = pio_claim_unused_sm(pio, false);
    if (sm < 0) {
        pio_close(pio);
        return NULL;
    }

    // TX program (.side_set 1):
    //   out pins, 4   side 0   ; drive a nibble on the 4 lanes, clock LOW
    //   nop           side 1   ; clock HIGH — RP2350 samples on this rising edge
    // autopull (threshold 32) consumes a whole 32-bit DMA word = 8 nibbles =
    // 4 bytes before refilling; when the FIFO drains the SM stalls on `out`
    // holding side 0 (clock idles low).
    static uint16_t instrs[2];
    instrs[0] = (uint16_t)(pio_encode_out(pio_pins, NUM_LANES) | pio_encode_sideset(1, 0));
    instrs[1] = (uint16_t)(pio_encode_nop() | pio_encode_sideset(1, 1));
    struct pio_program prog = {
        .instructions = instrs,
        .length = 2,
        .origin = -1,
    };
    unsigned offset = pio_add_program(pio, &prog);
    if (offset == PIO_ORIGIN_ANY) {
        pio_close(pio);
        return NULL;
    }

    // Claim the 8 data lanes + clock as PIO-driven outputs.
    for (unsigned i = 0; i < NUM_LANES; i++)
        pio_gpio_init(pio, data0_pin + i);
    pio_gpio_init(pio, clk_pin);
    pio_sm_set_consecutive_pindirs(pio, sm, data0_pin, NUM_LANES, true);
    pio_sm_set_consecutive_pindirs(pio, sm, clk_pin, 1, true);

    pio_sm_config c = pio_get_default_sm_config();
    sm_config_set_wrap(&c, offset, offset + 1);
    sm_config_set_sideset(&c, 1, false, false);   // 1 sideset bit = the data clock
    sm_config_set_sideset_pins(&c, clk_pin);
    sm_config_set_out_pins(&c, data0_pin, NUM_LANES);
    // shift_right=true => OSR LSB drives lane 0, LOW nibble of each byte first;
    // autopull on; threshold 32 => consume a full DMA word (4 bytes / 8 nibbles)
    // before refilling, so every byte of the frame is sent (threshold 8 would
    // emit only 1 byte per 32-bit word and drop the other 3).
    sm_config_set_out_shift(&c, true, true, 32);
    sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
    sm_config_set_clkdiv(&c, clkdiv);
    pio_sm_init(pio, sm, offset, &c);

    // DMA path to this SM. buf_size>=frame_bytes (config_xfer auto-uses the 32-bit
    // ioctl when >64 KB, so 8-panel 64 KB frames are fine), double-buffered.
    if (pio_sm_config_xfer(pio, sm, PIO_DIR_TO_SM, frame_bytes, 2)) {
        pio_close(pio);
        return NULL;
    }
    pio_sm_set_enabled(pio, sm, true);

    struct pioshim *h = malloc(sizeof *h);
    if (!h) {
        pio_close(pio);
        return NULL;
    }
    h->pio = pio;
    h->sm = sm;
    h->offset = offset;
    h->frame_bytes = frame_bytes;
    return h;
}

// Blocking DMA burst: clocks `nbytes` out the 8 lanes + data clock and returns
// when the transfer completes (0 ok, negative = piolib error). Per the piolib
// README this blocks the whole RP1 firmware interface for the burst duration —
// fine here: it's one short per-frame burst, like spidev's writebytes2.
int pioshim_send(struct pioshim *h, const void *buf, unsigned nbytes) {
    return pio_sm_xfer_data(h->pio, h->sm, PIO_DIR_TO_SM, nbytes, (void *)buf);
}

void pioshim_close(struct pioshim *h) {
    if (!h)
        return;
    pio_sm_set_enabled(h->pio, h->sm, false);
    pio_close(h->pio);
    free(h);
}
