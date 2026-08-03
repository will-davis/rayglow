# ROADMAP — queued workstreams

The living handoff doc between working sessions. Each section is one future
session's worth of work: enough grounding to start cold, open questions marked.
Sections get deleted when shipped (git history + `docs/design-history/` keep the
record). Recently shipped: the render-pipeline optimizations
([`docs/design-history/2026-07-13-optimization-paths.md`](docs/design-history/2026-07-13-optimization-paths.md),
GPU resolve pass + dma-heap zero-copy readback, 2.4× at scale 2); the
audio-feed v3 packet overhaul
([`docs/design-history/2026-07-13-audio-feed-v3.md`](docs/design-history/2026-07-13-audio-feed-v3.md),
8 flywheel bands + predictive beat tracker + 128-bin spectrum + key detection);
and the renderer **control plane**
([`docs/design-history/2026-07-14-control-plane.md`](docs/design-history/2026-07-14-control-plane.md),
`render/control.py` + `tools/rayglow_ctl.py` — the mutagen-free push dev loop +
media controls; supersedes the old §2 "now-playing" file-copy sketch); and
**remote render** ([REMOTE-RENDER-PLAN.md](REMOTE-RENDER-PLAN.md) §11 — GLSL on
ubuntu-server's RTX 4080, `--output net` ⇄ `rayglow.framesink`, credit-paced by
the Pi's DPI vblank; accepted on the wall 2026-08-03. Production display is the
ECP5 translator over DPI; the RP2350 path is the documented fallback).

---

## 1. Runtime brightness — firmware control surface, no bit-depth cost

> **Status 2026-08-03: PARKED — fallback-path only.** Production scan-out moved
> to the ECP5 translator, whose hardware brightness knob (SW5, contract §2a)
> covers the immediate need; this spare-nibble design applies only if the
> RP2350 `--output wall` path is revived. The *runtime/content-adaptive* half
> of the need survives and re-targets the DPI path — candidate design: a
> side-band in the DPI frame's discarded rows (the driver sends 480 rows, the
> FPGA captures 128 — rows 128+ are a free control channel). Tracked in the
> rayglow-fpga ROADMAP (Phase 4).

**Why:** at full drive the wall can light the room to the point of disorienting
on flashy shaders; dimming in the shader/pixel data spends wire codes
(half brightness ≈ 5.9 effective bits, 30% ≈ 4.3 bits — banding). OE dimming is
temporal: all 256 codes survive at any brightness, color ratios untouched.
Today the only knob is `OE_GAIN` + reflash; that's incidental, not
architectural — `Display::set_oe_gain` (`firmware/src/lib.rs:599`) just rewrites
the 8-entry `delays[]` RAM table the scan-out DMA reads live.

**Design — spare-nibble side-band (no protocol or wiring change):**

- The packed u16 cells use bits 0–11 (chain shift ≤ 9 + 3-bit RGB field);
  **bits 12–15 are don't-care to the scan-out**. Single-chain u8 cells likewise
  have bits 6–7 free.
- Packer stamps a brightness byte into the top nibbles of the first two cells
  of each frame; firmware's idle loop (already awake printing RTT stats) reads
  the active framebuffer's first cells after each flip and applies changes.
- Nibble value 0 ⇒ default `OE_GAIN` — old frames/firmware stay compatible in
  both directions.
- Fine steps: `delays[i] = ((1 << i) - 1) * num >> 4`-style 4.4 fixed point
  instead of the integer gain, so the control isn't 8 coarse steps. Clamp at
  the refresh-tradeoff ceiling (~gain 8 at 256 px / 37.5 MHz — see the
  `set_oe_gain` doc comment).
- Pi side: `--brightness` flag + a runtime source — the control plane is the
  natural home: add a `brightness` command to `render/control.py`'s protocol
  (the JSON extends cleanly) and/or a per-shader `brightness=` directive (item
  2). Mid-frame table rewrite worst-case shows one frame of mixed plane weights
  — gate on the flip if it's ever visible.

Deliverables: firmware (poll + fixed-point gain + clamp), `hub75.pack`/
`pack_single` stamping, CLI + runtime control, `tools/verify.py` extension
(golden frame with a nonzero brightness nibble), TUNING.md update.

## 2. Per-shader settings — directives the loader interprets

**Why:** some shaders want their own tuning — the motivating case is a shader
that needs high color fidelity at rest but "occasionally goes supernova" and
should carry a custom gamma (or a brightness cap, once item 1 exists).

- **The settings-namespace mechanism shipped** with `scale` as its first
  consumer: `textures.parse_settings` reads `// rayglow: key=val …` comments
  (parallel to `// iChannelN:`), and `build_shader` resolves scale as
  **override (CLI --scale / runtime `scale` command) > `// rayglow: scale=N`
  directive > config.DEFAULT_SCALE**. Runtime scale rides the control plane
  (`rayglow-ctl scale N|auto`); the override is sticky across switches until
  `auto`. A scale change reallocates FBOs, so it goes through the full-rebuild
  switch path (iTime restarts) — exactly the "on switch, not mid-flight" note
  below. So this item is now **gamma / fps / brightness** on the same
  `// rayglow:` line.
- Implementation notes:
  - **gamma**: make the resolve pass's gamma a uniform instead of a baked
    constant (uniform-value caching makes it free per-frame) — then per-shader
    gamma needs no shader regeneration on reload/switch. (Would also let a
    `gamma` control command change it live without a rebuild, unlike scale.)
  - **brightness**: rides the item-1 side-band; per-shader value = a stamp the
    packer applies on switch. Natural as both a `// rayglow: brightness=` value
    and a `brightness` control command.
  - **fps**: a `// rayglow: fps=` cap is a plain `args.fps` override on build.
  - Precedence for all of them mirrors scale: CLI/runtime override > shader
    directive > config default; reloads re-apply (same rule as channel
    directives). Extend `parse_settings` consumers in `build_shader` / the
    resolve pass, and add the matching control commands.

## 3. Remaining render-pipeline items (carried forward)

Full analysis in
[`docs/design-history/2026-07-13-optimization-paths.md`](docs/design-history/2026-07-13-optimization-paths.md).

- **`--scale` default** (`config.DEFAULT_SCALE`): resolve pass made supersampling
  cheap (no CPU cost); Will is running scale 1 on heavy shaders by hand. Now that
  per-shader `// rayglow: scale=` and a live `scale` control command exist (item
  2), the config default matters less — and remote render (2026-08-03) mooted
  the GPU budget entirely: the question is now "what does the *wall* reward"
  (panel pitch, BCM depth), answered empirically as Phase-4 headroom
  exploration on the 4080. Only a budget question again if Pi-local render
  returns.
- **UBO uniforms** (item 5's other half) — modest; only if profiling ever shows
  uniform upload mattering again.
- **Single-chain fold vectorization** (item 9) — only matters if a single-chain
  rig grows.
- **GPU pack pass** (bit-plane extraction as a fragment shader, readback = the
  wire stream) — **PARKED 2026-08-03**: the production path has no pack stage at
  all (the FPGA owns fold + gamma + BCM; frames leave the renderer as plain
  RGB). Applies only to the RP2350 `--output wall` fallback, where it remains
  the right optimization if that path is ever load-bearing again.

## 4. Feed-v3 tuning backlog (small, wall-time driven)

Not a session's worth — a watch-list from the v3 rollout, tune as material
shows problems:

- `ENV_TIERS` ballistics and `BAND_IMM_CLAMP` (sparse material can spike
  quiet bands); the per-band onset AGC's heavy tails (leaky-max normalizer is
  the fallback plan).
- `vol_imm`'s cross-FFT band mix (`VOL_LOW_COMP`) — AGC hides level, the mix
  is taste.
- Beat tracker on real music: octave locks (conf dips on relock), re-tempo
  ramp continuity on the milk-features card.

## 5. Wall v2 — SHIPPED as-built (2026-07-29…08-03), via the FPGA translator

**The wall is live**: 24 P4 tiles, 384×128, driven as **four** 6-panel chains by
the ECP5 translator over DPI (rayglow-fpga; 122.14 Hz source, 171.2 Hz scan at
SW5=6), rendered remotely on ubuntu-server (REMOTE-RENDER-PLAN.md §11). The
RP2350 two-chain plan this section originally described was **bypassed, not
built**: everything below the fold is kept for the record, but note honestly —
**12-deep chain SI was never validated**, so the `--output wall` fallback for
THIS wall is theoretical until someone runs the staged bring-up (6×2 first,
`fold_check.py`, `spi_test`). The one-chip SRAM analysis (12/chain = 75%,
ceiling 30 panels) stands. Firmware small win still open: zero-init `delays[]`
(.data→.bss) → ~14 KB image, faster flash-iterate.

<details>
<summary>Original RP2350 two-chain bring-up plan (superseded 2026-08-03)</summary>

The next physical wall, ~80% assembled as of 2026-07-16. **The transport question
is settled: ONE RP2350b drives all 24 tiles on two chains.** Host + firmware
support landed 2026-07-17; what remains is physical (cabling, power, SI).

**Geometry.** 6 wide × 4 tall of **P4-2121-64x32** tiles (P4 pitch, 2121 LEDs;
ambiguous vendor model, named by mirroring the old wall's `P6-3528-64x32`) =
**384 × 128 px**, physical **1536 × 512 mm**. `config.py` is now
`PANEL_COLS = 6`, `PANEL_ROWS = 4`; `PARALLEL_CHAINS` stays 2 (the HAT + the u16
engine are hardwired to two chains), so each chain carries 12 panels serpentined
over two panel rows and `render/hub75.to_chains` folds the wall into the two
768-wide electrical strips. Pixel count is **3× the old wall** (49 152 vs
16 384), so V3D render + readback scale with it — per-shader / runtime `scale`
(item 2) is the budget knob.

**Why one chip, not two (the ROADMAP was wrong).** The old estimate called
~384 KB of double-buffered framebuffer "borderline against the 520 KB SRAM". Two
errors: the linker's pool is **512 KB** (`memory.x`; banks 8/9 are separate 4 KB
regions), and the u16 cell packs *both* chains, so RAM scales with chain **width
only** (`fb_cells()` is independent of `CHAINS`) = 32 KB per panel-per-chain
double-buffered. Measured by linking `phase6-parallel --features two-chain` at
rising widths: **12/chain = 385 KB = 75%, and 15/chain (30 panels) is the last
that links; 16 overflows by 96 bytes.** So the two-RP2350 split and the FPGA
translator are both **unnecessary for this wall** — they stay the escape hatch
only if a *future* wall passes 30 panels or the SI math stops closing.

**The cost is refresh, not brightness.** Tripling chain width triples per-plane
shift time, diluting duty cycle (a plane costs `max(shift, lit)` — the row SM
waits on both). At the old `OE_GAIN` 64 the 768-wide wall sits at 39% duty vs the
old 72%. **`OE_GAIN` 192 restores 72% — identical brightness and average LED
current — by spending refresh: ~430 Hz → ~143 Hz**, far above flicker (more
visible to cameras than to eyes). Colour is untouched (binary plane ratios hold).
Link frame grows 64 KB → 192 KB, capping the wire at ~83 fps (over the 60 target).

**Remaining unknown: signal integrity on a 12-deep chain** — only 4 was ever
validated (§11.7). This is the whole risk now, and it can only be settled on the
bench. If it glitches, raise `DATA_CLK_DIV`: div 4 (18.8 MHz) still gives ~153 Hz
at gain 128. **Bring up staged**: set `PANEL_ROWS = 2` and cable 6×2 first — one
panel row per chain makes the fold the identity (`fold_check.py` proves this at
the packed-byte level), so a glitch there is SI, not the fold. Then `PANEL_ROWS =
4`. Firmware `PANELS_IN_CHAIN` must match the Pi's `config.CHAIN` (6 then 12) —
a fixed-size contract, and a mismatch desyncs the link **silently**;
`tools/fold_check.py` prints the value the Pi expects.

**Confirm the cabling** with `python -m rayglow.spi_test` (now runs over the
production `--transport pio`, no reflash): every panel carries cyan column-count
and orange row-count dots. IDs out of order ⇒ `CHAIN_ORDER`; a row upside-down ⇒
`ROW_ROTATE_180`; a row mirrored ⇒ `serpentine(first_row_reversed=)`. Whether a
strip starts at the left or right of its row depends on where the HAT plugs in
and **cannot** be derived from the desk.

**Power.** 2× **300 W 5 V 60 A** transformers (2nd arrived 2026-07-17) — 600 W /
120 A, comfortable for audio-reactive content but **not** sustained all-white 24
tiles, so brightness capping (item 1) matters more at this size. Split the wall
by supply on the **horizontal midline** so the PSU split coincides with the
chain A / chain B split — each rail's return current then stays inside its own
chain's domain. Bond both PSU (−) at one star point; see POWER-AND-GROUNDING.

**Frame.** 2020 extruded-aluminium T-slot: 2 horizontal + 4 vertical members,
tied to the panels by 3D-printed connectors under `hardware/3dprint/` (P4-*).
Those parts are **WIP — tolerances still iterating; do NOT `git add` them until
the PoC is fully built** (the tracked STLs there are the HAT enclosure only).

**Small win available:** the framebuffers land in `.data`, not `.bss` — the
non-zero `delays[]` const-init in `DisplayMemory::new` drags the whole struct
into flash, so the image is ~406 KB and boot memcpy's 384 KB. Zero-init `delays`
and fill it at runtime in `Display::new` (`set_oe_gain` already overwrites it
immediately) ⇒ ~14 KB image, much faster flash-iterate on the bench.

</details>
