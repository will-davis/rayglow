# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in
this repository.

## Orientation

RayGLow is a three-stage audio-reactive LED-wall pipeline: **desktop** (audio → feature
packets) → **Raspberry Pi 5** (Shadertoy GLSL → packed frames) → **RP2350** (zero-CPU
PIO+DMA HUB75 scan-out) → a 256×64 panel wall. **Read `README.md` first** — it has the
pipeline diagram and the repo map. The pieces:

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
    RP2350. Entry: `python -m rayglow.render`.
  - `rayglow/fake_sender.py` — music-free test harness, same packet struct.
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
- **`docs/design-history/`** — superseded design docs kept for provenance (MilkDrop
  reverse-engineering, the RP2350 PROJECT-PLAN, the build-history brain-dump).

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
  - *The feature packet* — `sender/sender.py`'s `PACKET_FMT` (4236 B, v2) and
    `rayglow/feed/receiver.py` must change together and bump `VERSION`. The receiver
    dispatches on `(version, byte length)` and accepts v0 (556 B) + v1 (564 B, `sub =
    bass` for v0) + v2 (the richer spectrum/chroma/beat/stereo feed; v2-only fields
    default to zero for older senders). The full rules-that-look-wrong list (linear band
    thirds, equalize-on, the deliberately inconsistent `analyze_sub`, deferred
    `sounddevice` import) is in `sender/CLAUDE.md`.
  - *The link frame* — `rayglow/render/hub75.py` packs a 64 KB bit-plane stream that the
    firmware's RX DMA drops into its framebuffer with zero touch-up (the same bytes over
    the parallel bus or SPI; the layout is defined by `Display::render` in
    `firmware/src/lib.rs`). The packer and the firmware are a **1:1 port of each other**;
    change one and you change both. `tools/verify.py` builds a Rust golden frame and
    asserts they're byte-identical — run it after any layout/gamma change.
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
mount step anymore. This repo (+ git history + `docs/design-history/`) is the durable
shared memory across the machines — prefer writing knowledge into tracked files.
Machine-specific addresses/paths are generalized to placeholders in tracked files; real
values (and the mutagen session details) live in the gitignored `LOCAL-SETUP.md`
(template: `LOCAL-SETUP.example.md`).

## Verifying changes

- Renderer numerics, no hardware: `python -m rayglow.render <shader> --dry-run 120
  --no-listen` → frame stats + a GIF (works on the desktop's EGL too).
- Sender: `cd sender && uv run sender.py --debug` → 1 Hz status line.
- Packer ≡ firmware: `uv run --with numpy tools/verify.py` (needs `cargo`).
- Firmware builds: `cd firmware && cargo build` (nightly + `thumbv8m.main-none-eabihf`).
- On the panel: `sudo ~/venv/bin/python -m rayglow.render
  rayglow/render/presets/milk-verbose.glsl` with the sender running — the reference card
  reacts to audio.

No test suite or linter. Validation is empirical (sine tones, dry-run GIFs, the panel).
