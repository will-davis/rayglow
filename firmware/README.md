# RayGLow firmware (RP2350)

Zero-CPU HUB75 RGB-matrix scan-out engine for the **RP2350** (PIO + DMA),
driving 24× 64×32 panels (384×128) over two parallel chains of 12 — one chip for
the whole wall, at 385 KB of its 512 KB SRAM (the measured ceiling is 15
panels/chain = 30 panels; see [`../ROADMAP.md`](../ROADMAP.md) §5). This is the
downstream end of the RayGLow pipeline: the Pi 5 renders + packs frames and
ships them over the 4-lane parallel PIO bus (SPI as fallback — see the
repo-root [`README.md`](../README.md)); this firmware receives them and drives
the panels with deterministic, jitter-free timing. The crate (named
`rp2350-rgb-driver`, its original standalone-repo name) implements the design in
[`docs/design-history/PROJECT-PLAN.md`](../docs/design-history/PROJECT-PLAN.md)
one verifiable phase at a time.

## Status

| Phase | What | State | Bin |
|---|---|---|---|
| **0** | Bring-up: boot block, flash/boot path, blink + `defmt` RTT | ✅ hardware-verified | `phase0-blink` |
| **1** | Port kjagiello `hub75-pio-rs` to `rp235x-hal`, 1 chain × 1 panel | ✅ hardware-verified (RGB bars, gamma good) | `phase1-panel` |
| **3a** | Single-chain depth: 4 daisy-chained panels → 256×32 (SI test §11.7) | ✅ hardware-verified — SI clean at 37.5 MHz | `phase3-row` |
| **2** | Widen data path to 2 parallel chains (`out pins, 16`, `u16` cell) | engine done + init-verified — needs a 2nd panel on chain B | `phase2-dualchain` |
| **4** | Bulk frame repack + animation, double-buffered | ✅ hardware-verified — ~159 fps @ 64×64 (debug) | `phase4-anim` |
| 3 | Both chains × 4 deep → full 256×64 + coordinate mapper | superseded — the full wall runs via Phase 6 `--features two-chain` | — |
| **5** | Pi 5 → RP2350 SPI link (PIO SPI-slave RX + DMA into the inactive FB) | ✅ hardware-verified — now the **fallback** transport (`--transport spi`) | `phase5-spi` |
| **X** | **Single-chain** stop-gap: full 256×64 on ONE 8-panel serpentine chain via the spare Adafruit HAT (level-shifter only), SPI-fed. Uses the `u8` single-chain engine (`src/single.rs`, `Display1`) — 64 KB frames (not 128), `Display::flip` | retired with the custom HAT (kept for A/B rigs) | `phase-experimental` |
| **6** | **Pi 5 → RP2350 4-lane parallel link** (RP1-PIO TX → PIO1 RX + DMA into the inactive FB), single- or two-chain via the `two-chain` cargo feature | ✅ hardware-verified on the custom HAT — **the production transport** | `phase6-parallel` |
| QC | Custom-HAT board bring-up / pin-by-pin self-test | utility, not a milestone | `qc-test` |

> Note: `phase3-row` (single-chain 256×32) is run *before* the Phase 2 two-chain
> widening — a deliberate reorder to retire the signal-integrity risk early using
> the already-assembled panel row. The pixel clock is tunable via `DATA_CLK_DIV`
> in that binary.

> **Phase X (`phase-experimental`)** is a deliberate *deviation*, not a milestone:
> it lights the whole wall while the custom two-chain HAT is in fab, using the one
> Adafruit RGB Matrix HAT as a pure 3.3→5 V buffer and a single U-shaped chain of
> all eight panels (electrically 512×32, driven as **chain A only**; GP6–11 idle).
> It reuses the unchanged, verified engine at `W=512`. Cost: the `u16` cell makes
> the frame **128 KB** (half is the idle chain B) and refresh is ~½ the two-chain
> wall. The rpi5 must pack with the single-chain serpentine fold (bottom row
> 180°-rotated) — see the binary's header and `rayglow/render/hub75.py`.

## Layout

```
firmware/
├── Cargo.toml             # rp235x-hal 0.4, defmt, one [[bin]] per phase
├── rust-toolchain.toml    # pinned nightly + thumbv8m.main-none-eabihf target
├── memory.x               # RP2350 flash/RAM map + IMAGE_DEF boot-block sections
├── build.rs               # puts memory.x on the linker search path
├── .cargo/config.toml     # target, probe-rs runner, linker rustflags
├── src/
│   ├── lib.rs             # core HUB75 engine: DisplayMemory, PIO programs, DMA chain
│   ├── single.rs          # single-chain u8-cell engine (half-size frames)
│   ├── dma.rs             # low-level per-channel DMA register access (ported kjagiello)
│   └── lut.rs             # CIE/gamma LUT (ported kjagiello; matches render/hub75.py)
└── src/bin/
    ├── phase0_blink.rs    # bring-up: boot block + blink + RTT
    ├── phase1_panel.rs    # 1 chain × 1 panel (kjagiello port)
    ├── phase2_dualchain.rs# widen data path to 2 parallel chains
    ├── phase3_row.rs      # 1 chain × 4 panels = 256×32 (SI test)
    ├── phase4_anim.rs     # full 256×64 animated + bulk repack, double-buffered
    ├── phase5_spi.rs      # Pi 5 → RP2350 SPI link (fallback transport)
    ├── phase6_parallel.rs # Pi 5 → RP2350 4-lane parallel link (PRODUCTION; two-chain feature)
    ├── phase_experimental.rs # single-chain serpentine stop-gap (Adafruit-HAT era)
    └── qc_test.rs         # custom-HAT board bring-up self-test
```

## Toolchain bootstrap (one time)

If your system has Arch's pacman `rust` (stable, no `rustup`, no bare-metal
target), note that embedded RP2350 work needs `rustup` to manage a nightly
toolchain and the `thumbv8m.main-none-eabihf` (Cortex-M33) target.

```fish
# 1. Swap the system stable Rust for rustup (Arch-native; replaces `rust`).
sudo pacman -S rustup

# 2. rustup reads rust-toolchain.toml in this dir and installs the pinned
#    nightly + the thumbv8m target + components automatically on first cargo run.
#    (Force it now if you like:)
rustup show

# 3. probe-rs for flashing + RTT logging over the CMSIS-DAP debugprobe.
cargo install probe-rs-tools --locked

# Optional: UF2 / BOOTSEL path (no probe, no RTT logs)
# cargo install elf2uf2-rs --locked     # or build picotool from AUR
```

> Alternative if you'd rather not touch the pacman `rust` package: install
> rustup from <https://rustup.rs> (`rustup-init` into `~/.cargo`, no sudo). It
> shadows the system `rustc` via `~/.cargo/bin` on `PATH`. The pacman route is
> cleaner long-term (one toolchain manager, Arch-native).

## Build & flash

```fish
cd firmware

# Build only (host, no hardware needed):
cargo build --bin phase0-blink

# Flash + attach RTT logs (CMSIS-DAP debugprobe wired to the target's SWD):
cargo run --bin phase0-blink
```

`cargo run` invokes the configured runner `probe-rs run --chip RP235x`: it
flashes the ELF, resets, and streams `defmt` logs back to this terminal.

**Phase 0 is done when:** the LED blinks at 1 Hz and `blink N` lines stream over
RTT.

### Hardware notes

- **Board:** **Waveshare RP2350-PiZero (RP2350B, 16 MB flash)** — the project's
  main board. It has **no user-controllable LED** (only a hardwired power LED)
  and **no WS2812**. So Phase 0's heartbeat is the **`defmt` RTT counter**; for a
  *visible* blink, wire an external LED (+ ~330 Ω) from **GP25** to GND, or scope
  GP25. GP25 is free in the §6 pin map (HUB75 = GP0–18, chains C/D reserve
  GP19–21), so the wiring survives into later phases.
- **debugprobe:** a second Pico flashed with the CMSIS-DAP `debugprobe`
  firmware. Wire its SWD pins to the target's `SWCLK`/`SWDIO`/`GND`. probe-rs
  auto-detects it.
- **No probe?** Hold BOOTSEL, plug USB, and `picotool load -u -v -x -t elf
  target/thumbv8m.main-none-eabihf/debug/phase0-blink` (no RTT logs that way).

## RP2350-specific notes

- **Boot block, not boot2.** The RP2350 Boot ROM scans the first 4 KiB of flash
  for an `IMAGE_DEF` block (`#[link_section = ".start_block"]` in each binary,
  sections defined in `memory.x`). There is no RP2040-style `boot2`.
- **Target is `thumbv8m.main-none-eabihf`** (Cortex-M33), *not* the RP2040's
  `thumbv6m`.
- **GPIO erratum E9** (input pads latching high) is irrelevant to our outputs
  but will matter for the Phase 5 SPI link input pins — handle there.
