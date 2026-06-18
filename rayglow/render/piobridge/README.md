# piobridge — 4-lane parallel Pi 5 → RP2350 link (Workstream 3)

Replaces the 1-lane SPI transport with a **4-lane source-synchronous parallel
bus** driven by the Pi 5's RP1 PIO block, lifting the link off the critical path
(a 32 KB frame goes from ~6.6 ms at 40 MHz SPI to ~1.3 ms at clkdiv 2).

(4 lanes, not 8: the RP2350 board exposes GP0–27 and the scan-out engine owns
GP0–18, leaving only GP19–27 for the link — see `phase6_parallel.rs`.)

The stream is **byte-identical** to the SPI path — same `hub75.py` packer output,
same CS-framing + READY handshake — so only the wire changes, not the protocol.

## Pieces
- `pio_shim.c` → `libpioshim.so` — flat C ABI over RP1 `piolib` (whose API is
  `static inline`, so ctypes can't call it directly). Drives `out pins, 4` + a
  sideset data clock, DMA-fed.
- `rayglow/render/pio_out.py` — `PioOut`, the `SpiOut`-compatible transport that
  loads the shim and frames each burst with CS (gpiozero) + READY.
- Firmware: `firmware/src/bin/phase6_parallel.rs` (`cargo run --bin phase6-parallel`).

## Pin map (BCM ↔ RP2350 GP)
| signal      | rpi5 BCM        | RP2350b GP   | notes |
|-------------|-----------------|--------------|-------|
| DATA0..3    | GPIO12..GPIO15  | GP20..GP23   | 4 contiguous lanes, `out pins,4` base = GPIO12 |
| DCLK        | GPIO20          | GP24         | Pi-driven data clock (sideset) |
| CS          | GPIO21          | GP25         | active-low frame boundary (gpiozero output) |
| READY       | GPIO25 (in)     | GP26 (out)   | RP2350 → Pi: armed-and-waiting (same GP as SPI) |
| GND         | —               | —            | a return beside the lane bundle; keep short |

GP19 + GP27 are spare on the RP2350. Confirm none of GPIO12–15/20/21 are reserved
on your Pi (PWM/I2S/PCM can claim some) before wiring.

## Build & run (on the Pi)
1. Build piolib as a library (once):
   ```fish
   cd .reference/rpi5/utils/piolib
   cmake -DBUILD_SHARED_LIBS=1 . ; and make
   ```
   Ensure `/dev/pio0` exists and is group-accessible (piolib README: udev rule +
   `gpio` group) so it runs without root if desired.
2. Build the shim:
   ```fish
   cd rayglow/render/piobridge ; and make
   ```
   (`make PIOLIB=/path/to/piolib` if it lives elsewhere.) Produces `libpioshim.so`
   here; `pio_out.py` loads it from this directory.
3. Flash the firmware: `cd firmware ; and cargo run --bin phase6-parallel`.
4. Run the renderer with the parallel transport:
   ```fish
   cd /tmp   # local CWD — lgpio FIFO can't live on the network mount
   sudo ~/venv/bin/python -m rayglow.render <shader> --transport pio --pio-clkdiv 4
   ```

## Bring-up order (don't skip)
1. **Logic-analyzer first.** At a high `--pio-clkdiv` (slow), send a known ramp and
   confirm the bytes land correctly on the RP2350 side (a debug `info!` of the
   first framebuffer cells, or picotool). Two things to confirm:
   - **Nibble order** — each byte goes out as two nibbles (low then high under the
     default swap). If every byte's nibbles come out swapped, run with
     `--pio-no-nibble-swap` (no reflash needed).
   - **Lane↔bit** — within a nibble, lane i = bit i (DATA0 = bit 0). If mirrored,
     reverse the physical lane wiring (DATA0↔DATA3, DATA1↔DATA2).
2. **Then on-panel**, compare a static test pattern against the SPI path — they
   must be pixel-identical (same bytes). Then lower `--pio-clkdiv` toward 1 and
   watch `rx fps` / drops; the READY handshake self-paces exactly as SPI did.

`--transport spi` (default) stays the proven fallback throughout.
