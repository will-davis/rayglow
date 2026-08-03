# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in
this repository.

## Orientation

RayGLow is a three-stage audio-reactive LED-wall pipeline: **desktop** (audio → feature
packets) → **Raspberry Pi 5** (Shadertoy GLSL → packed frames) → **RP2350** (zero-CPU
PIO+DMA HUB75 scan-out) → a 384×128 panel wall (6×4 of 64×32 tiles; wall v1 was
256×64). **Read `README.md` first** — it has the pipeline diagram and the repo map.
The pieces:

- **`sender/`** — the desktop daemon (`sender.py`): captures audio, sends feature
  packets. Standalone uv project (numpy + sounddevice). Has its own `README.md` +
  `CLAUDE.md`; the MilkDrop-DSP-port invariants live there. Sub-projects:
  `esp32-mic/` (I2S-microphone sender firmware) + `espnow-dongle/` (ESP-NOW→UDP
  bridge), each with its own README.
- **`rayglow/`** — the Pi package, installed editable into a uv venv (`~/venv`):
  - `rayglow/feed/` — the audio-feature feed (packet `receiver`, `FeatureState`,
    rig `config`). Neutral, shared, dependency-free; the future yaml-config target.
  - `rayglow/render/` — **the live renderer**: headless EGL + GLES3 GPU rendering of
    Shadertoy-dialect shaders; a GPU resolve pass (`output.py`) downsamples + gammas +
    orients each frame, read back zero-copy through a dma-heap mmap (`dmabuf.py`,
    glReadPixels fallback), then `hub75.py` packs it and `pio_out.py` (default;
    `piobridge/` C shim over RP1 piolib) or `spi_out.py` (fallback) ships it to the
    RP2350. Entry: `python -m rayglow.render`. A wall run also opens a TCP
    **control plane** (`control.py`, port 5006): the mutagen-free push dev loop +
    media controls, driven by `tools/rayglow_ctl.py` (`push`/`load`/`next`/`pause`/
    `loop`/`scale`/`status`). `--no-control` skips it. Supersample scale is
    per-shader: `--scale` > a `// rayglow: scale=N` directive (`textures.parse_settings`)
    > `config.DEFAULT_SCALE`, live-adjustable via `rayglow-ctl scale`.
  - `rayglow/fake_sender.py` — music-free test harness, same packet struct.
  - `rayglow/link.py` + `rayglow/framesink.py` — **remote render**
    (REMOTE-RENDER-PLAN.md): the renderer runs on any GPU box (`--output net`,
    `render/net_out.py` — NVIDIA headless via `--egl device`) and ships resolved
    RGB frames over UDP to the Pi's `python -m rayglow.framesink`, which
    reassembles latest-wins into `drm_out` page flips and returns one credit per
    vblank — the flip is the master clock, so the sender can't outrun or queue
    ahead of the wall (bounded at `--net-window` frames). `link.py` is the wire
    contract both ends import; `tools/link_check.py` locks it.
  - `rayglow/spi_test.py` — static test pattern over the SPI fallback (no GL) to
    isolate link/firmware; `tools/pio_ramp.py` is the parallel-bus equivalent for
    byte order.
- **`firmware/`** — the **RP2350 Rust firmware** (`rp235x-hal`, no_std): a port of
  kjagiello's `hub75-pio-rs` widened to two parallel chains, brought up in verifiable
  phases (`src/bin/phaseN_*.rs`; **phase 6 = the production 4-lane parallel link**,
  built `--features two-chain`; phase 5 = the SPI fallback). Has its own README +
  `THIRD-PARTY.md`.
- **`hardware/`** — the custom level-shifting HAT (KiCad project, Gerbers, net/pinout spec).
- **`tools/`** — `verify.py`: proves `render/hub75.py` is byte-identical to the firmware.
  `fold_check.py`: proves the serpentine fold (`hub75.to_chains`) + the geometry/SRAM
  contract from the desk, and prints the firmware `PANELS_IN_CHAIN` the Pi expects.
  `feed_check.py`: proves the feature-packet contract (sender ⇄ receiver ⇄ fake_sender);
  its `--live` mode pretty-prints real packets for sine-tone band verification.
  `rayglow_ctl.py`: the control-plane client (`push`/`load`/media controls);
  `control_check.py` locks its wire contract; `nvim-rayglow.lua` is the save-hook.
  `link_check.py`: locks the remote-render frame link (`rayglow/link.py` —
  fragments, credits, pacing) over loopback, no hardware.
- **`docs/design-history/`** — superseded design docs kept for provenance (MilkDrop
  reverse-engineering, the RP2350 PROJECT-PLAN, the build-history brain-dump).
- **`ROADMAP.md`** — queued workstreams, one session's worth each (runtime brightness
  side-band, per-shader directives). When Will says
  "pick up the next item" start there. (The audio-v3 packet overhaul shipped 2026-07;
  its brief + rationale are archived in `docs/design-history/`.)

## What this is (and isn't)

RayGLow is **its own project**, not a fork. MilkDrop = a ported DSP front-end (its
auto-gain semantics are now the project's protocol); Shadertoy = a compatibility surface
the renderer implements so site shaders run unchanged; kjagiello's `hub75-pio-rs` = the
firmware's ported architecture base. See `ATTRIBUTION.md`. Don't reintroduce
"milkdrop"/"shadertoy" as identities — but note the strings survive legitimately in two
places: the packet magic `MILK`/`0x4D494C4B`, and the `milk` / `audio` iChannel **spec
names** in shaders. Those are wire/shader-facing names, not package names — don't rename
them.

## Invariants that look like bugs but aren't

- **Two cross-machine contracts, both must stay in lockstep:**
  - *The feature packet* — `sender/sender.py`'s `PACKET_FMT` (2996 B, v3) and
    `rayglow/feed/receiver.py` (+ `rayglow/fake_sender.py`) must change together and
    bump `VERSION`. The receiver dispatches on `(version, byte length)` and accepts
    v0 (556 B) + v1 (564 B, `sub = bass` for v0) + v2 (4236 B) + v3 (the 8-band
    flywheel/theta/beat/key feed; fields a version doesn't carry default to
    zero/neutral for older senders). `tools/feed_check.py` roundtrip-checks the
    contract — run it after any packet change. The full rules-that-look-wrong list
    (linear band thirds, equalize-on legacy path, NO equalize on the v3 bands, the
    deliberately inconsistent `analyze_sub`, deferred `sounddevice` import) is in
    `sender/CLAUDE.md`.
  - *The link frame* — `rayglow/render/hub75.py` packs a 192 KB bit-plane stream that the
    firmware's RX DMA drops into its framebuffer with zero touch-up (the same bytes over
    the parallel bus or SPI; the layout is defined by `Display::render` in
    `firmware/src/lib.rs`). The packer and the firmware are a **1:1 port of each other**;
    change one and you change both. `tools/verify.py` builds a Rust golden frame and
    asserts they're byte-identical — run it after any layout/gamma change. The frame is a
    **fixed-size contract**: the Pi's `config.CHAIN` must equal the firmware's
    `PANELS_IN_CHAIN` (`phase6_parallel.rs`) or the link desyncs *silently* —
    `tools/fold_check.py` prints the value the Pi expects.
- **Three widths, and conflating them is the classic bug.** `WALL_WIDTH` (384) is the
  logical picture; `CHAIN_WIDTH` (768) is one chain's electrical strip and IS the
  firmware's `W`; `WALL_HEIGHT` (128) is the picture's height. A HUB75 chain is
  electrically one 32-row strip however many panels hang off it, so a chain spanning two
  panel rows is **serpentined** — `render/hub75.to_chains` folds the 384×128 wall into
  two 768×32 strips (stacked = the 64-row frame `pack()` eats). **The firmware knows
  nothing about the wall's shape.** When each chain covers one panel row
  (`PANEL_ROWS_PER_CHAIN == 1` — wall v1, and the staged 6×2 step) the fold is the
  identity and returns the frame untouched. `tools/fold_check.py` proves the identity at
  the packed-byte level plus losslessness/coverage/cable-adjacency — run it after any
  geometry change. Which *end* of a row a strip starts at is physical (where the HAT
  plugs in) and can only be confirmed on the wall with `python -m rayglow.spi_test`.
- **Gamma is applied exactly once, and where depends on the readback mode.**
  `config.PACK_GAMMA` (2.1, mirrors firmware `lut.rs`; the firmware never touches
  streamed frames) is baked into the GPU resolve pass by default, so `run_wall` feeds
  the packer `hub75.LUT_IDENTITY` — giving it the CIE LUT too would double-correct.
  In `--readback legacy` the old contract holds: LINEAR readback, packer applies its
  CIE LUT (that pairing is what `tools/verify.py` pins to the firmware). Dry-runs
  stay LINEAR (`--gamma`, default 1.0) in every mode.
- **The resolve pass owns orientation too.** The GL bottom-left origin flip and
  `config.FLIP_V/FLIP_H` are baked into its sampling coordinates on wall runs; only
  legacy mode still flips on the CPU. Readback is zero-copy dma-heap mmap
  (`render/dmabuf.py`) where Mesa+dma-heaps exist, `glReadPixels` elsewhere — never
  "fix" the auto-fallback message on desktop dry-runs; it's expected.
- **Import direction:** `render` imports *up* into `feed`; keep `feed` dependency-free of
  the renderer (no GL/SPI imports at module load). SPI deps (`spidev`/`gpiozero`/`lgpio`)
  are Pi-only, optional (`.[pi]`), and imported lazily so the desktop dry-run never needs
  them.
- **Deploy is editable-install, not PYTHONPATH:** `uv pip install --python
  ~/venv/bin/python -e '.[pi]'`. `sudo` scrubs env (so PYTHONPATH would need `-E`) but
  respects the installed package. Hardware mode keeps root for GPIO + to re-read `.glsl`
  on hot reload, so the clone must live where root can read it (under `~`).

## Working across the machines

There are three deploy targets: the **desktop** runs `sender/`; the **Pi** runs the
`rayglow` package (editable install); the **RP2350** is flashed with the firmware
(`cargo run` / `probe-rs`, see `firmware/README.md`). The desktop clone is the single
source of truth: **mutagen** continuously one-way-syncs it to the Pi's `~/rayglow`
(a second mutagen session pushes the private, out-of-repo `~/Projects/rayglow-shaders`
to the Pi's `~/presets`). Editing here IS deploying — there is no `git pull` or sshfs
mount step anymore. For the **live shader-dev loop**, mutagen's ~5–20s propagation
is bypassed: `tools/rayglow_ctl.py push <file>` (or the nvim save-hook,
`tools/nvim-rayglow.lua`) ships the edited shader straight to the running renderer's
control plane (TCP 5006) for sub-100ms feedback; mutagen stays the durable
background library sync. This repo (+ git history + `docs/design-history/`) is the durable
shared memory across the machines — prefer writing knowledge into tracked files.
Machine-specific addresses/paths are generalized to placeholders in tracked files; real
values (and the mutagen session details) live in the gitignored `LOCAL-SETUP.md`
(template: `LOCAL-SETUP.example.md`).

## Verifying changes

- Renderer numerics, no hardware: `python -m rayglow.render <shader> --dry-run 120
  --no-listen` → frame stats + a GIF (works on the desktop's EGL too).
- Sender: `cd sender && uv run sender.py --debug` → 1 Hz status line.
- Feature packet ≡ across sender/receiver/fake_sender: `uv run --with numpy
  tools/feed_check.py` (`--live` to watch real packets, e.g. under sine tones).
- Control-plane wire contract (client ⇄ server framing, no hardware): `uv run
  tools/control_check.py`.
- Remote-render frame link (fragments ⇄ credits, latest-wins, credit pacing —
  loopback, no hardware): `uv run --with numpy tools/link_check.py`.
- Serpentine fold + the geometry/SRAM contract (no hardware): `uv run --with numpy
  tools/fold_check.py` — also prints the `PANELS_IN_CHAIN` the firmware needs.
- Beat tracker: `cd sender && uv run beat.py` → click-track lock table.
- Packer ≡ firmware: `uv run --with numpy tools/verify.py` (needs `cargo`).
- Firmware builds: `cd firmware && cargo build` (nightly + `thumbv8m.main-none-eabihf`).
- On the panel: `sudo ~/venv/bin/python -m rayglow.render
  rayglow/render/presets/milk-verbose.glsl` with the sender running — the reference card
  reacts to audio.

No test suite or linter. Validation is empirical (sine tones, dry-run GIFs, the panel).
