# Tuning Guide — rayglow over the RP2350 SPI link

This is the knob reference for the split rig: **rpi5 renders + packs**, sends
packed bit-plane frames over SPI to the **rp2350b**, which drives the 256×64
HUB75 wall with a zero-CPU PIO+DMA engine.

## Mental model: two sides, one link

| | **rpi5** (`rayglow` package) | **rp2350b** (`firmware/`) |
|---|---|---|
| Owns | **Content** — pixels, gamma, orientation, resolution, fps | **Timing** — HUB75 pixel clock, BCM/OE brightness, refresh |
| Change via | `config.py` + CLI flags | editing a `.rs` const |
| Takes effect | next run (no reflash) | **only after `cargo build` + reflash** |

The SPI link carries *only frame content* (a 64 KB packed bit-plane buffer). So
anything about *how the panels are lit* (brightness, clock, refresh) is firmware
and needs a reflash; anything about *what is drawn* is rpi5-side and is live.

---

## rpi5 / rayglow knobs — no reflash

### CLI flags (`python -m rayglow.render <shader> [flags]`)

| Flag | Default | What it does / when to reach for it |
|---|---|---|
| `--spi-hz` | 24 MHz | SPI clock. **Higher = more frame throughput, but more wiring SI risk.** Raise it once the HAT is in; on the breadboard keep it modest. |
| `--scale` | 4 | GPU supersample factor (1–16). Higher = smoother/antialiased, **costs GPU + readback time → lower fps.** Drop to 2 or 1 if a heavy shader chugs. |
| `--fps` | 60 | Frame-rate cap. The link is self-paced by the rp2350b's READY line; this just stops you rendering frames nobody asked for. |
| `--gamma` | 1.0 | **Leave at 1.0.** The packer applies the real (CIE) gamma downstream; setting this non-1 double-corrects. |
| `--width` / `--height` | 256 / 64 | Render size. Defaults to the full wall for `spi`; only touch for experiments. |
| `--duration` | 0 (forever) | Stop after N seconds. |
| `--channel0..3`, `--no-listen` | — | Bind iChannels (audio / milk / noise / image); `--no-listen` = synth audio only. |

### `config.py` (`rayglow/feed/config.py`) — the structural settings

| Constant | Now | Meaning |
|---|---|---|
| `ROWS`,`COLS`,`CHAIN`,`SPI_PARALLEL` | 32,64,4,2 | Panel geometry. `SPI_WIDTH`/`SPI_HEIGHT` derive from these (256×64). Change only if the panel count changes. |
| `SPI_GAMMA` | 2.1 | The CIE gamma the **packer** applies. Lower = brighter mids / less contrast, higher = deeper blacks. **Must stay equal to the firmware `lut.rs` gamma** so the look matches firmware-rendered demos and the byte-match golden. |
| `SPI_FLIP_H` / `SPI_FLIP_V` | True / True | Orientation. Both True = 180° (this wall: HUB75 input on the right, inverted mount). Flip these if you re-mount or re-cable. |
| `SPI_BITDEPTH` | 8 | BCM planes. **Must equal firmware `B`.** |

> Gamma note: on the **SPI path the firmware does NOT gamma-correct** — it just
> DMAs the pre-packed bit-planes to the panels. Gamma is applied entirely by the
> rpi5 packer (`SPI_GAMMA`). The firmware's own `lut.rs` gamma only matters for
> firmware-rendered demos like `phase4-anim`.

---

## rp2350b / firmware knobs — require a reflash

Edit `firmware/src/bin/phase5_spi.rs`, then from the `firmware/` dir:

```fish
cargo run --release --bin phase5-spi   # builds + flashes + runs (probe-rs)
```

| Const | Now | Meaning / tradeoff |
|---|---|---|
| `OE_GAIN` | 6 | **The brightness knob.** Scales the BCM output-enable (lit) intervals. Higher = brighter, roughly linearly, up to ~8 at 256-wide before it starts eating refresh rate. Raise it if the wall is dim (e.g. at the slow breadboard clock). |
| `DATA_CLK_DIV` | `(6,0)` = 12.5 MHz | **HUB75 pixel clock** = sysclk / (2·div). Faster (smaller div) = brighter + higher refresh, but more signal-integrity risk down the daisy chain. `(2,0)` = 37.5 MHz is the target **once the HAT's '245 buffers are in**; `(6,0)` is the current breadboard-safe setting. |
| `W`,`H`,`B` | 256,32,8 | Geometry; **must match** rayglow `config` (per-chain H=32, B=8). |

---

## Quick recipes

- **Too dim** → raise `OE_GAIN` (firmware, reflash). After the HAT, also/instead
  lower `DATA_CLK_DIV` for a faster, brighter clock.
- **Flicker / low refresh** → lower `DATA_CLK_DIV` div (faster clock). Needs clean
  SI (the HAT) to push fast.
- **SI artifacts** (down-chain vertical bars, scan-half noise) → raise
  `DATA_CLK_DIV` div (slower clock). This is the current breadboard mitigation;
  the HAT fixes the SI so you can go fast again.
- **Heavy shader chugs** → drop `--scale` (4→2→1).
- **Want more fps** → raise `--spi-hz` (needs SI headroom), and/or lower `--scale`,
  and/or lower `DATA_CLK_DIV` div (frees refresh time).
- **Colors look off** → confirm `--gamma` is 1.0 for spi and `SPI_GAMMA` matches
  the firmware LUT.
- **Image flipped / mirrored** → `SPI_FLIP_H` / `SPI_FLIP_V` in `config.py`.

---

## Post-HAT checklist (when the level-shifter board arrives)

The HAT gives clean buffered 5 V drive + a ground plane, which removes the SI
ceiling we're working under on the breadboard:

1. **Reflash** with `DATA_CLK_DIV` back toward `(2,0)` (37.5 MHz) → ~3× the
   brightness and refresh, and the down-chain bars should clear.
2. **Re-tune `OE_GAIN`** — a faster clock is brighter, so you may be able to
   lower it.
3. **Raise `--spi-hz`** toward 50 MHz for higher frame throughput.

---

## Diagnostics

- `python -m rayglow.spi_test [--flipv --fliph --spi-hz N]` — static test pattern
  straight through `pack → SPI → wall`, no GL. Corner colors (white TL, red TR,
  green BL, blue BR) verify orientation; clean gradient + corners verify the link.
- `python -m rayglow.render <shader> --dry-run` — render to a GIF headlessly,
  no hardware, to preview geometry/shader.
- Firmware RTT log (`cargo run --bin phase5-spi`) prints `rx fps` = frames the
  rp2350b is committing — a live confirmation the link is locked.
