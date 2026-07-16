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
media controls; supersedes the old §2 "now-playing" file-copy sketch).

---

## 1. Runtime brightness — firmware control surface, no bit-depth cost

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
  2), the config default matters less — decide it after more wall time,
  especially once the bigger v2 wall (item 5) sets the GPU budget.
- **UBO uniforms** (item 5's other half) — modest; only if profiling ever shows
  uniform upload mattering again.
- **Single-chain fold vectorization** (item 9) — only matters if a single-chain
  rig grows.
- **GPU pack pass** (bit-plane extraction as a fragment shader, readback = the
  64 KB wire stream) — the prep that makes the next wall cheap on the Pi side.
  Note the RP2350 SRAM ceiling analysis assumed ONE chip; Will's current plan
  is **two RP2350s on the Pi 5** for the big wall, which halves per-chip
  framebuffer needs (512×128 double-buffered fits again) and needs the link/
  packer split per chip instead of the FPGA translator.

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

## 5. Wall v2 — the 384×128 panel + the transport split

The next physical wall, ~80% assembled as of 2026-07-16. Bigger in every axis;
the open question is how to drive it.

**Geometry.** 6 panels wide × 4 tall of **P4-2121-64x32** tiles (P4 pitch, 2121
LEDs; ambiguous vendor model, named by mirroring the current wall's
`P6-3528-64x32`) = **384 × 128 px**, physical **1536 × 512 mm**. In
`config.py` terms that's `CHAIN = 6`, `PARALLEL_CHAINS = 4` (today's wall is
4 / 2 = 256×64) — the renderer derives everything from those, so the Pi side is
a config bump, not a code change. Pixel count is **3× today's** (49 152 vs
16 384), so the V3D render + readback cost scales with it — the new per-shader /
runtime `scale` control (item 2) is the budget knob.

**Power.** 2× **300 W 5 V 60 A** AC/DC transformers (the 2nd arrives 2026-07-17;
bring-up/testing starts then). At P4 full-white the 24 tiles pull well past one
supply, so the two rails split the wall — see POWER-AND-GROUNDING for the
star-ground + HV-distribution rules that must extend to a two-PSU feed.

**Frame.** 2020 extruded-aluminium T-slot: 2 horizontal + 4 vertical members,
tied to the panels by 3D-printed connectors under `hardware/3dprint/` (P4-*).
Those parts are **WIP — tolerances still iterating; do NOT `git add` them until
the PoC is fully built** (the tracked STLs there are the HAT enclosure only).

**Open decision — transport (resolve during bring-up).** Driving 24 tiles /
384×128 exceeds one RP2350's comfortable envelope: the packed frame is ~192 KB
(3× the current 64 KB), and double-buffered ~384 KB against the 520 KB SRAM is
borderline once code/stack/DMA are counted. Two paths, decided as the wall comes
up (see the FPGA-translator memory + the item-3 "GPU pack pass" note):
- **Two RP2350s on the Pi 5** — split the wall (e.g. one chip per 2-chain half),
  each with its own link + packer. Halves per-chip framebuffer, reuses the whole
  existing firmware/packer stack. Needs the packer/link split per chip.
- **FPGA translator** (ULX3S / ECP5, HDMI→HUB75) — the escape hatch if the
  per-chip PIO/RAM math stops closing as the wall grows further.
