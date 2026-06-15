# RP2350 HUB75 RGB-Matrix Driver — Project Plan

> Staging-area plan for a ground-up firmware + custom-PCB build that drives **8× 64×32 HUB75 panels (256×64 total)** from a **Raspberry Pi RP2350**, fed frames by a Raspberry Pi 5. This document is the entry point for an implementation agent. Read it top to bottom, then read the reference material called out in §12 before writing code.

---

## 1. Context & goal

The author currently drives an RGB LED display from a Raspberry Pi using the `hzeller/rpi-rgb-led-matrix` library (a copy is in `reference-repositories/rpi-rgb-led-matrix/` for reference) with a custom GLSL shader generating frames. The pain point is timing jitter: a Linux SoC bit-banging GPIO fights the scheduler, needs a dedicated RT core, and the Pi 5's RP1 southbridge made it worse.

**The new architecture splits render from scan-out:**

```
  Raspberry Pi 5                         RP2350 (this project)
 ┌────────────────┐   frames (SPI)     ┌──────────────────────────┐
 │ GLSL shader →  │ ─────────────────► │ ingest → bit-plane pack → │ ──► HUB75 ──► 8 panels
 │ RGB framebuffer│   ~1.5–3 MB/s      │ PIO + DMA scan-out (0 CPU)│      (256×64)
 └────────────────┘                    └──────────────────────────┘
```

The RP2350 runs a deterministic PIO state-machine + DMA engine that pushes pixels to the panels with cycle-exact timing and **zero CPU involvement in the refresh loop**, leaving both cores free to ingest frames from the Pi 5. This is the whole point of the redesign — do not adopt any approach that puts the CPU back in the per-plane refresh loop.

**Primary goals**
- Drive **256×64** (two rows of four 64×32 panels) at **≥30 fps content, flicker-free (≥150 Hz refresh)**.
- Match or exceed current color quality: **CIE1931-corrected, target 8-bit now → 11-bit later** per channel.
- Custom PCB to interface the dev board to the panels, with **four HUB75 connectors** for headroom/upgradability.
- Clean, transparent, well-understood design (the author values learning the *why*, not black boxes).

**Out of scope for the first milestones (but reserve the design):** the Pi 5 → RP2350 link. Build the scan-out engine first against a locally generated test pattern, then add the link (§10).

---

## 2. Confirmed decisions (do not re-litigate)

| Decision | Choice | Rationale |
|---|---|---|
| Architecture base | **kjagiello `hub75-pio-rs`** (Rust, rp2040-hal) | 3-SM split with a dedicated PIO-timed OE/BCM engine + self-chaining DMA = genuinely zero-CPU refresh; cleanest BCM; scales to high bit depth. The hard, easy-to-get-wrong part is done right. |
| Multi-channel reference | **pitschu `RP2040matrix-v2`** | Its V2/`8080` layout already drives **two HUB75 channels in parallel** packed into one PIO word — the exact mechanism we extend. Also a good template for full-frame→bit-plane repack. |
| Language / stack | **Rust** (`rp235x-hal`) | Continuation of kjagiello; author is growing in Rust and values the type safety. Accept the nightly toolchain cost (§7, §11). |
| MCU | **RP2350B** via **Waveshare RP2350-PiZero** dev board | Pi-Zero form factor suits the 2-row panel layout; 48 GPIO leaves ~25 spare for future chains C/D (→ 16 panels). |
| Display topology | **2 parallel chains × 4 panels** (256×64) | Confirmed comfortable on RP2350 (see §4). Two rows = two chains, one chain per row. |
| Custom PCB role | **HAT/carrier on top of the dev board** | Dev board provides power/crystal/flash/USB; the PCB only does level-shifting + connectors + panel power (§9). Greatly reduces board complexity. |
| Bit depth | Start **8-bit**, design buffers/PIO to scale to **11-bit** | Matches current pipeline; RP2350 SRAM has the room. |
| Pi 5 link transport | **SPI** (deferred) | RP2350 USB is Full-Speed only (~1.5 MB/s) — insufficient for 256×64×3×30 fps. SPI at 25–50 MHz is ample (§10). |

---

## 3. Hardware target — RP2350 / RP2350-PiZero

From the datasheets in `reference-documents/`:

- **RP2350B**, QFN-80, **48 GPIO** (GPIO0–47). Dual Cortex-M33 + dual Hazard3 RISC-V @ **150 MHz** (overclockable). IO fixed/typical **3.3 V**.
- **520 KB SRAM** in 10 banks; **3 PIO blocks / 12 state machines** (32 instructions per SM); **16 DMA channels** *(verify exact count in `rp235x-pac`)*.
- **RP2350-PiZero** (schematic: `reference-documents/RP2350-PiZero.pdf`): onboard DVI/HDMI consumes **GPIO32–39**, SD card uses **GPIO30–31, 40–46**, USB-C, QSPI flash. The 40-pin header breaks out **GPIO0–21+**. We will not use the onboard HDMI/SD, so those GPIO are irrelevant; **GPIO0–18 (our 19 signals) are available and contiguous** — confirm exact header pin↔GPIO mapping against the schematic before routing the PCB (§9, §11).

**RP2350-specific gotchas the agent must handle:**
1. **Boot image block.** RP2350 binaries require an embedded `IMAGE_DEF` block (not the RP2040 `boot2`). `rp235x-hal` provides this via a `#[link_section = ".start_block"]` `hal::block::ImageDef` (or embassy's equivalent). Get a blink working first to prove the boot/flash path.
2. **GPIO erratum (RP2350 E9):** input-mode pads can latch high via the internal pull. Irrelevant for our outputs (we drive '245 inputs), but relevant later for the **SPI link's input pins** — use external pulls / follow the erratum workaround there.
3. PIO on RP2350 is a superset of RP2040's; kjagiello's PIO programs port largely unchanged.

---

## 4. Display topology & feasibility (the "why", so it isn't hand-waved)

**Layout:** two rows × four 64×32 panels = **256 W × 64 H**. Wired as **two parallel chains**, each chain = four panels daisy-chained = **256 W × 32 H, 1/16 scan** *(confirm panel scan rate — 64×32 is usually 1/16 → 4 address lines A–D; some are 1/8. See §11)*.

- **Chain A** = top row, **Chain B** = bottom row.
- **Shared** across both chains: CLK, LATCH, OE, address lines A–D.
- **Per-chain:** R1,G1,B1,R2,G2,B2 — **confirmed:** each panel is a single HUB75 connector whose two RGB triplets independently feed the panel's top half (R1G1B1, rows 0–15) and bottom half (R2G2B2, rows 16–31), clocked together. This is exactly what kjagiello's `XXBGRBGR` framebuffer tuple packs, so no change is needed.
- Both chains are clocked **simultaneously** by one data SM; the framebuffer word carries both chains' RGB bits. Parallel chains cost nothing in refresh time — that's the key lever.

**Pin budget:** 12 RGB (2×6) + CLK + LATCH + OE + 4 address = **19 GPIO**.

**Memory** (RP2350 = 520 KB): one packed word per (column × address-row × bit-plane), holding both chains' 12 RGB bits.
- words = 256 cols × 16 addr × `B` planes = `4096·B`.
- 8-bit, double-buffered: ≈ **128 KB** (32-bit words) / 64 KB (16-bit words).
- 11-bit, double-buffered: ≈ **176 KB**. Comfortable; 16-bit would still fit.

**Refresh:** kjagiello measures ~**2100 Hz @ 24-bit on a 64-wide** panel. Per-chain width here is 256 (4×) → shift time 4× → ~**525 Hz @ 24-bit**, far higher at 8–11 bit. Need >~150 Hz for flicker-free + 30–60 Hz content. **Large margin.** Conclusion: 2 chains × 4 panels is the comfortable design point, not a compromise.

**Pixel clock:** kjagiello runs the data SM at `clkdiv (2,0)` (sys/2 = 75 MHz instr, 2 instr/pixel → 37.5 MHz pixel). Over four daisy-chained panels through '245 buffers and a backplane, that is likely too fast. **Plan to tune `clkdiv` for a ~20–25 MHz pixel clock and validate signal integrity on hardware** (the refresh budget above easily absorbs the slower clock).

---

## 5. Firmware architecture

Adopt kjagiello's three-state-machine model **unchanged in structure**; widen only the data path.

### 5.1 PIO state-machine allocation (use PIO0; PIO1/PIO2 reserved for the SPI link later)
- **Data SM** — clocks RGB out + drives CLK (sideset). *Modification:* `out pins, 12` (two chains' RGB) instead of `out pins, 8`. (Reference `hub75-pio-rs/src/lib.rs:230-265`.)
- **Row SM** — sets the 4 address lines, pulses LATCH, and choreographs timing via IRQ handshakes with the data and OE SMs. Largely unchanged. (`lib.rs:267-309`.)
- **OE SM** — generates binary-weighted display intervals by consuming a tiny `delays[]` array (`(1<<i)-1` ticks per plane) streamed by its own DMA channel. **This is the BCM engine — keep it as-is.** It is agnostic to data-path width. (`lib.rs:50-58`, `:311-333`.)

### 5.2 DMA
Keep kjagiello's **four-channel design with two self-retriggering loop channels** (`chain_to` ping-pong) so refresh runs forever with zero CPU: framebuffer-feed + framebuffer-loop, OE-feed + OE-loop. (`lib.rs:335-492`.) Port the direct register pokes in `hub75-pio-rs/src/dma.rs` to the **`rp235x-pac`** register layout; verify DMA channel/register differences vs RP2040.

### 5.3 Framebuffer memory layout (the main change from kjagiello)
kjagiello packs **1 byte per (column, plane, addr)** holding one chain's 6 bits (`XXBGRBGR`). We need **two chains = 12 bits**, so widen the cell:
- Use a **16-bit (or 32-bit) packed word** per (column, plane, addr): `[chainB: B2 G2 R2 B1 G1 R1][chainA: B2 G2 R2 B1 G1 R1]`, with the bit order matching the data SM's `out pins, 12` GPIO mapping (chain A on GP0–5, chain B on GP6–11).
- Borrow the packing pattern from pitschu's two-channel `8080` repack: `RP2040matrix-v2/hub75_BCM.c:461-505` shows exactly how to fold two channels' per-bit values into one word. Adapt it to kjagiello's plane-major buffer layout (`lib.rs:60-121` documents that layout).
- Keep kjagiello **double-buffering** + `commit()` on buffer flip (`lib.rs:516-529`).

### 5.4 Frame ingest — replace the per-pixel API
kjagiello exposes a per-pixel `embedded-graphics` `DrawTarget`/`set_pixel` (`lib.rs:534-561`). At 16384 px × ≥30 fps that is far too slow and the wrong shape for streaming. **Replace it with a bulk repack**: take a whole RGB frame (RGB888 or RGB565) and expand it to all bit-planes in one pass — pitschu's `hub75_update()` (`hub75_BCM.c:419-515`) is the template. Pin this repack to **core 1** so core 0 can service the SPI/USB link; or use DMA/interpolator assist. Keep the **CIE1931 gamma LUT** (kjagiello `src/lut.rs`) to preserve current color quality.

### 5.5 Coordinate mapping
Author intends a fixed **2 rows × 4 cols** arrangement, but keep the logical-pixel `(x,y)` → (chain, panel, column, half) mapping **table/param-driven** so panel order, chain assignment, and serpentine vs. progressive wiring can change without touching the PIO. This mirrors the "pixel mapper" concept in `rpi-rgb-led-matrix` (`lib/pixel-mapper.cc`) — a good reference for the mapping math.

---

## 6. GPIO / pin map (verified against RP2350-PiZero header)

kjagiello requires the RGB group and the address group each be **electrically consecutive** GPIO (PIO `out pins` drives consecutive GPIO from a base). The RP2350-PiZero 40-pin header is **confirmed to use standard Raspberry Pi BCM numbering** (verified: physical pin 12 = GPIO18). All of RP2350 GP0–27 are broken out; onboard HDMI/SD use GP30+ and don't conflict.

| Signal | RP2350 GPIO | Header pin (Pi BCM) | Notes |
|---|---|---|---|
| Chain A: R1 G1 B1 R2 G2 B2 | GP0–GP5 | 27, 28, 3, 5, 7, 29 | data SM `out pins` base = GP0 |
| Chain B: R1 G1 B1 R2 G2 B2 | GP6–GP11 | 31, 26, 24, 21, 19, 23 | `out pins, 12` spans GP0–11 |
| Address A B C D | GP12–GP15 | 32, 33, 8, 10 | row SM `out pins, 4` (1/16 scan, 4 lines) |
| CLK | GP16 | 36 | data SM sideset |
| LATCH | GP17 | 11 | row SM sideset |
| OE | GP18 | 12 | OE SM sideset |

19 pins, GP0–GP18, all present on the header. **Routing reality:** BCM numbering makes these electrically contiguous (ideal for PIO) but **physically scattered** across the header — the HAT fan-out to the '245s is a 2-layer routing job, not a tidy row. Reserve GP19–GP21 (header pins 35, 38, 40) for chains C/D RGB if a 16-panel future is wanted; reserve a separate contiguous group for the SPI link (§10). If a different electrically-contiguous base eases routing, it can be reassigned freely — only the *consecutiveness* matters to the PIO, not the specific base.

---

## 7. Toolchain & build

- **Rust nightly** (kjagiello uses `#![feature(generic_const_exprs)]`, `const_for]`). Pin a known-good nightly in `rust-toolchain.toml`. Target **`thumbv8m.main-none-eabihf`** (Cortex-M33), **not** the RP2040's `thumbv6m`.
- HAL: **`rp235x-hal`** (replaces `rp2040-hal`). Consider `embassy-rp` (RP2350 supported) if its DMA/PIO ergonomics are preferable — but kjagiello's low-level DMA approach maps most directly onto `rp235x-hal` + `rp235x-pac`. PIO assembly via the `pio`/`pio-proc` crates is HAL-agnostic and ports as-is.
- Flash/debug: **`probe-rs`** with a debug probe (a second Pico as a `debugprobe` works), or UF2 over USB BOOTSEL for quick flashing. `defmt` + RTT for logging.
- Set up the **RP2350 boot `ImageDef` block** early; prove the flash/boot path with a blink before any PIO work.
- Use **uv** for any host-side Python tooling (test-pattern generators, frame senders) per author preference. No pip/venv/conda.

---

## 8. Implementation phases (each milestone is verifiable on real hardware)

> No simulator — validation is against the actual dev board + panels, exactly as in the reference projects.

**Phase 0 — Bring-up.** Blink on RP2350-PiZero via `rp235x-hal`; confirm toolchain, boot block, flashing, RTT logging. *Done when:* LED blinks, `defmt` logs appear.

**Phase 1 — Port kjagiello as-is to RP2350, ONE chain, ONE panel.** Move from `rp2040-hal`→`rp235x-hal`, port `dma.rs` registers, get the unmodified 3-SM/4-DMA single-chain driver lighting a single 64×32 panel with a static test pattern. *Done when:* one panel shows correct gamma-corrected color, zero CPU in refresh (verify cores idle). **This de-risks the hardest port before adding width.**

**Phase 2 — Widen the data path to TWO parallel chains.** `out pins, 12`; widen the framebuffer cell to a packed 12-bit word; update the bulk packer (pitschu pattern). Drive two single panels (the two chains) in parallel. *Done when:* both chains show independent correct images, refresh unchanged.

**Phase 3 — Extend each chain to 4 daisy-chained panels (256×64 full wall).** Implement the param-driven coordinate mapper; tune `clkdiv` for clean 256-wide clocking; verify refresh ≥150 Hz and no smearing/ghosting at chain end. *Done when:* full 256×64 test patterns render correctly and flicker-free.

**Phase 4 — Bulk frame ingest + double-buffer.** Full-frame repack on core 1, `commit()` on vsync, local animated test source (e.g., port a plasma/flame). Measure max fps. *Done when:* smooth ≥30 fps animation from a CPU-generated source.

**Phase 5 — Pi 5 → RP2350 SPI link.** §10. *Done when:* frames generated on the Pi 5 render on the wall at ≥30 fps.

**Phase 6 — Custom PCB.** §9. Can begin in parallel after Phase 3 fixes the pin map.

---

## 9. Custom PCB — HAT/carrier for the dev board

**Scope:** the PCB is a **HAT that mounts on the RP2350-PiZero's 40-pin header**. The dev board supplies 3.3 V logic, crystal, flash, USB, and core power — so the PCB does **not** need the RP2350 power/crystal/decoupling design from `RP-008280` (that doc is only relevant if a future rev integrates a bare RP2350B). The HAT handles three things:

1. **Level shifting (3.3 V → 5 V) via `SN74AHCT245`** (author has 10 on hand — correct part: TTL-threshold inputs accept 3.3 V, push-pull 5 V CMOS outputs for HUB75's 5 V logic; tpd ~10 ns is fine at ≤25 MHz).
   - Fix each '245 direction (`DIR` tied), `OE`→GND (always enabled).
   - **Buffer per connector**, and fan out a **separately buffered copy of CLK/LATCH/OE/ADDR to each HUB75 connector** rather than driving four panels' worth of input capacitance from one buffer pin — clock integrity over the backplane is the thing that bites people. Budget: ~2 '245s per chain (6 RGB + shared control split across connectors); 10 chips comfortably covers 2 active chains + the 2 spare connectors.
   - Keep CLK traces short and roughly length-matched; series termination resistors (~22–33 Ω) on CLK and data near the '245 outputs are cheap insurance.
2. **Four HUB75 connectors** (2×8 IDC, standard HUB75/HUB75E pinout). Wire all four for upgradability even though only two are driven initially; route the spare two to GP19–21 + shared control so a firmware change lights them later.
3. **Panel power distribution.** Panels are the big load — **5 V at high current**. Measured: one 150 W / 30 A supply runs **four** panels fine (~37 W/panel peak), so **all eight need ~300 W / 60 A** — plan for two 150 W supplies (or one 60 A unit). Key rules:
   - Do **not** power panels from the dev board.
   - **Panel 5 V goes to each panel's own power lugs, never through the HUB75 ribbon** — the ribbon carries logic only. Inject power per row/chain with fat wire; add bulk capacitance (e.g. 1000 µF) near each panel-power injection point.
   - **Common all grounds:** panel-supply GND ↔ '245 5 V-rail GND ↔ dev board GND. The HAT itself only needs the 5 V rail to feed the '245s (a few mA) and that shared ground reference.
   - Per-'245 100 nF decoupling.

**Connector/pinout note:** confirm the HUB75 variant (HUB75 vs HUB75E — E adds the 5th address line E on what is otherwise GND/unused). 64×32 1/16-scan panels need only A–D.

---

## 10. Pi 5 → RP2350 link (deferred to Phase 5, reserve the design)

- **Transport: SPI**, Pi 5 as controller, RP2350 as peripheral. RP2350 USB is Full-Speed (~1.5 MB/s) — insufficient for 256×64×3 B×30 fps ≈ 1.47 MB/s with no margin. SPI at 25–50 MHz gives 3–6 MB/s, ample for 60 fps RGB888; RGB565 halves it.
- On the RP2350, receive via a **dedicated PIO block (PIO1/PIO2) + DMA** straight into the inactive framebuffer-source, or the hardware SPI peripheral + DMA. Use a spare contiguous GPIO group; mind the RP2350 input erratum (§3.2).
- Define a tiny framing protocol (magic/seq/length + payload, double-buffer flip on full frame). Consider a "dirty rectangle" or RGB565 mode later if bandwidth or Pi-side cost matters.
- The Pi 5 side can reuse the existing GLSL→framebuffer pipeline; replace the `rpi-rgb-led-matrix` output stage with an SPI writer (`spidev`).

---

## 11. Open questions / confirm before/while building

1. ~~Panel scan rate~~ — **RESOLVED: 1/16 scan, 4 address lines A–D.** Single HUB75 connector per panel; R1G1B1 feeds top half (rows 0–15), R2G2B2 the bottom half (rows 16–31).
2. ~~Header mapping~~ — **RESOLVED: standard Pi BCM numbering** (pin 12 = GPIO18). GP0–GP18 all broken out; see verified map in §6.
3. ~~HUB75 variant~~ — **RESOLVED: plain HUB75** (not HUB75E); 4 address lines suffice.
4. ~~Panel power~~ — **RESOLVED (sizing): ~300 W / 60 A for 8 panels** (two 150 W supplies). See §9.3 wiring rules.
5. **`generic_const_exprs` friction** — if the nightly const-generics path proves painful, fall back to a fixed compile-time config (concrete W/H/B consts) rather than fully generic types. Acceptable; the dimensions are fixed for this build.
6. **embassy vs bare rp235x-hal** — decide in Phase 1 based on DMA/PIO ergonomics; either is fine.
7. **Pixel-clock signal integrity** — the open empirical risk: validate clean 256-wide clocking through the '245s + backplane at the chosen `clkdiv` (target ~20–25 MHz); add series termination on CLK/data if there's ghosting. Determined on hardware in Phase 3.

---

## 12. Reference index

> The three prior-art repositories and the vendor datasheets below were studied
> during the build but are **not vendored** into RayGLow (license hygiene + repo
> weight). Clone/download them yourself if you want to follow the references; the
> upstreams are credited in [`ATTRIBUTION.md`](../../ATTRIBUTION.md).

**Repositories** (clone separately):
- **`kjagiello/hub75-pio-rs`** (MIT) — *the architecture base.* `src/lib.rs` (3-SM + 4-DMA design, framebuffer layout, the three PIO programs, DMA setup, double-buffer, set_pixel), `src/dma.rs` (register pokes ported to `rp235x-pac`), `src/lut.rs` (CIE/gamma LUT). RayGLow's `firmware/src/{lib,dma,lut}.rs` are a port of these.
- **`pitschu/RP2040matrix-v2`** — *parallel-channel + bulk-repack reference.* `hub75_BCM.c` (the two-channel repack), the `*_BCM.pio` programs, `include/hub75.h` (two-data-group pin layout). **Non-commercial license — used as a learning reference only; no code copied.**
- **`hzeller/rpi-rgb-led-matrix`** (GPL-2.0) — *background + coordinate-mapping math.* `lib/pixel-mapper.cc`, `lib/framebuffer.cc` for how BCM/bit-planes and panel mapping are done in the mature C++ library.

**Datasheets** (download from Raspberry Pi / Waveshare):
- RP2350 datasheet (1380 pp — PIO, DMA, GPIO, clocks; consult by section).
- Pico 2 datasheet + RP2350 product brief (package/GPIO/SRAM/PIO summary).
- "Hardware design with RP2350" (only needed for a bare-RP2350B PCB rev).
- RP2350-PiZero dev board schematic (header pin↔GPIO mapping).

---

## 13. One-paragraph summary for the implementing agent

Port kjagiello's `hub75-pio-rs` from `rp2040-hal` to `rp235x-hal` on an RP2350-PiZero (RP2350B), keep its zero-CPU 3-state-machine + 4-DMA refresh engine and PIO-timed BCM **exactly**, and make one structural change: widen the data path from one HUB75 chain to **two parallel chains** (`out pins, 12`, a 12-bit packed framebuffer word) so it drives **256×64 = two rows of four 64×32 panels**. Replace the per-pixel draw API with a **bulk frame→bit-plane repack** (pitschu pattern) pinned to core 1, preserving the CIE1931 gamma LUT. Validate in phases on real hardware (single panel → two chains → 4-deep chains → animation → SPI link from the Pi 5). The custom PCB is a **HAT** over the dev board doing only `SN74AHCT245` level-shifting, four HUB75 connectors, and dedicated 5 V panel power — no MCU support circuitry. GPIO: RGB GP0–11, address GP12–15, CLK/LAT/OE GP16–18.

---

## 14. Instrumentation & validation coverage (bench-gear reality check)

> The full bench inventory (source of truth) lives outside this repo in a private lab manifest. This section records only the **project-specific verdict**: can each §8 phase actually be *measured* with gear on hand?

**Bring-up target note (good news):** the firmware can be developed through Phase 4 on a **Pico 2 (RP2350A)** already on hand. The pin map needs only GP0–GP18 (19 pins; daisy-chaining adds *panels*, not pins), which fits the Pico 2's exposed GPIO, and RP2350A has **identical SRAM (520 KB) and PIO (3 blocks/12 SM)** to the RP2350B — it is not memory- or PIO-limited. The Waveshare RP2350B board is only needed for spare GPIO (future chains C/D + the SPI link). Prototype early phases on the Pico 2 to de-risk before the HAT exists.

**Phase → measurement coverage:**

| Phase | Needs | On hand? |
|---|---|---|
| 0 Bring-up | flash / boot / log | ✅ Waveshare USB-JTAG bridge or a spare Pico as `debugprobe`, USB |
| 1–2 one panel → two chains | rail check, slow-signal capture | ✅ FNIRSI DMM; PicoScope 2204A adequate for the ≤~5 MHz control/latch/address lines |
| 3 256-wide @ ~20–25 MHz | **CLK signal integrity** (§11.7) | 🔴 **GAP** — both scopes (10 MHz / ~200 kHz BW) are far below the ≥100–200 MHz needed (rule of thumb: scope BW ≥ 5× clock). Blocks the SI validation. |
| 4 Animation / sustained load | panel + '245 thermals, current under load | 🟡 partial — DC current via DMM/DC-clamp; no thermal camera yet |
| 5 Pi 5 → RP2350 SPI link | multi-channel bus timing | 🟡 no dedicated logic analyzer; interim stopgap = flash a spare Pico as a sigrok LA |

**Gaps & cheap fixes (priority order):**
1. 🔴 **≥100 MHz oscilloscope** — the one hard blocker (Phase 3 only). A 12-bit MSO (Rigol DHO900 / Siglent SDS800X HD) with the logic-probe option *also* closes the analyzer gap below. Until then, Phases 0–2 proceed; defer the §11.7 SI question to when the scope lands.
2. 🟡 **Logic analyzer** — DSLogic U3Pro16, or repurpose a spare Pico (sigrok) for free in the interim.
3. 🟡 **Constant-current bench supply** — no current-limit safety net on first HAT power-on (wall-wart workflow). Mitigate with an inline fuse (fuse kit on hand) until a programmable CC/CV supply is added.

**Power: covered.** 5 V @ 150 W (30 A) + 300 W (60 A) = **90 A**, comfortably over the ~60 A the 8-panel wall needs (§9.3).
