# ROADMAP — queued workstreams

The living handoff doc between working sessions. Each section is one future
session's worth of work: enough grounding to start cold, open questions marked.
Sections get deleted when shipped (git history + `docs/design-history/` keep the
record). Recently shipped: the render-pipeline optimizations
([`docs/design-history/2026-07-13-optimization-paths.md`](docs/design-history/2026-07-13-optimization-paths.md),
GPU resolve pass + dma-heap zero-copy readback, 2.4× at scale 2) and the
audio-feed v3 packet overhaul
([`docs/design-history/2026-07-13-audio-feed-v3.md`](docs/design-history/2026-07-13-audio-feed-v3.md),
8 flywheel bands + predictive beat tracker + 128-bin spectrum + key detection).

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
- Pi side: `--brightness` flag + a runtime source (file-watch a brightness
  file? fold into the item-2 control interface / item-3 per-shader settings).
  Mid-frame table rewrite worst-case shows one frame of mixed plane weights —
  gate on the flip if it's ever visible.

Deliverables: firmware (poll + fixed-point gain + clamp), `hub75.pack`/
`pack_single` stamping, CLI + runtime control, `tools/verify.py` extension
(golden frame with a nonzero brightness nibble), TUNING.md update.

## 2. Shader playback — "now playing" + playlist control

**Will's sketch:** the Pi renderer plays a single reserved file (e.g.
`~/presets/now-playing.glsl`) forever; switching shaders = copying a file over
it. Manual `cp` while developing; later a will-desktop-side controller that
walks a playlist. Hot reload already gives instant, crash-proof pickup
(compile errors keep the last good shader on the wall).

- **Mutagen synergy:** the desktop already one-way-syncs both shader trees to
  the Pi (~1 s). A desktop-side controller can just copy into the synced tree —
  zero new transport, works from the couch, and the ssh-into-the-Pi step
  disappears entirely.
- **Known gotcha — multipass:** buffer passes are discovered at *build* time
  only (`build_shader` scans for `foo.bufA.glsl` siblings once); hot reload
  recompiles existing passes but can't add/remove them. So copying a multipass
  shader over a single-pass now-playing file won't pick up its buffers today.
  Fix options: (a) teach `maybe_reload` to rescan siblings and rebuild the toy
  when the pass set changes (state resets — acceptable for a shader *switch*),
  or (b) the controller copies `now-playing.bufA.glsl` etc. and touches the
  image file last. (a) is the robust one.
- Escalation path if file-copy ever feels limiting: a tiny UDP/OSC control
  socket on the renderer (select shader by path, set brightness, skip) — and
  that's also the natural HA integration point later. Start with the file; it
  composes with everything.

## 3. Per-shader settings — directives the loader interprets

**Why:** some shaders want their own tuning — the motivating case is a shader
that needs high color fidelity at rest but "occasionally goes supernova" and
should carry a custom gamma (or a brightness cap, once item 1 exists).

- Extend the existing comment-directive mechanism (`// iChannelN: spec`,
  parsed in `textures.parse_directives`) with a settings namespace, e.g.:
  `// rayglow: gamma=1.8 brightness=0.5 fps=90`. Comments keep pasted
  Shadertoy sources valid and survive hot reload naturally.
- Implementation notes:
  - **gamma**: make the resolve pass's gamma a uniform instead of a baked
    constant (uniform-value caching makes it free per-frame) — then per-shader
    gamma needs no shader regeneration on reload/switch.
  - **brightness**: rides the item-1 side-band; per-shader value = a stamp the
    packer applies on switch.
  - **scale**: needs FBO reallocation ⇒ full toy rebuild; support it on shader
    *switch* (--loop / now-playing swap), not mid-flight hot reload.
  - Precedence: CLI flag > shader directive > config default, and reloads
    re-apply (same rule as channel directives).

## 4. Remaining render-pipeline items (carried forward)

Full analysis in
[`docs/design-history/2026-07-13-optimization-paths.md`](docs/design-history/2026-07-13-optimization-paths.md).

- **`--scale` default**: resolve pass made supersampling cheap (no CPU cost);
  Will is running scale 1 on heavy shaders by hand. Decide default after more
  wall time; per-shader `scale=` (item 3) may make the default moot.
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

## 5. Feed-v3 tuning backlog (small, wall-time driven)

Not a session's worth — a watch-list from the v3 rollout, tune as material
shows problems:

- `ENV_TIERS` ballistics and `BAND_IMM_CLAMP` (sparse material can spike
  quiet bands); the per-band onset AGC's heavy tails (leaky-max normalizer is
  the fallback plan).
- `vol_imm`'s cross-FFT band mix (`VOL_LOW_COMP`) — AGC hides level, the mix
  is taste.
- Beat tracker on real music: octave locks (conf dips on relock), re-tempo
  ramp continuity on the milk-features card.
