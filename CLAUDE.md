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
  `CLAUDE.md`; the MilkDrop-DSP-port invariants live there.
- **`rayglow/`** — the Pi package, installed editable into a uv venv (`~/venv`):
  - `rayglow/feed/` — the audio-feature feed (packet `receiver`, `FeatureState`,
    rig `config`). Neutral, shared, dependency-free; the future yaml-config target.
  - `rayglow/render/` — **the live renderer**: headless EGL + GLES3 GPU rendering of
    Shadertoy-dialect shaders, then `hub75.py` packs each frame and `spi_out.py` ships
    it to the RP2350. Entry: `python -m rayglow.render`.
  - `rayglow/fake_sender.py` — music-free test harness, same packet struct.
  - `rayglow/spi_test.py` — static SPI test pattern (no GL) to isolate link/firmware.
- **`firmware/`** — the **RP2350 Rust firmware** (`rp235x-hal`, no_std): a port of
  kjagiello's `hub75-pio-rs` widened to two parallel chains, brought up in verifiable
  phases (`src/bin/phaseN_*.rs`; phase 5 = the production SPI link). Has its own README +
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
  - *The feature packet* — `sender/sender.py`'s `PACKET_FMT` (564 B, v1) and
    `rayglow/feed/receiver.py` must change together and bump `VERSION`. The receiver
    accepts v0 (556 B) + v1, substituting `sub = bass` for v0. The full
    rules-that-look-wrong list (linear band thirds, equalize-on, the deliberately
    inconsistent `analyze_sub`, deferred `sounddevice` import) is in `sender/CLAUDE.md`.
  - *The SPI frame* — `rayglow/render/hub75.py` packs a 64 KB bit-plane stream that the
    firmware's `Display::render` (`firmware/src/lib.rs`) drops into its framebuffer with
    zero touch-up. The packer and the firmware are a **1:1 port of each other**; change
    one and you change both. `tools/verify.py` builds a Rust golden frame and asserts
    they're byte-identical — run it after any layout/gamma change.
- **The packer owns gamma; the render readback stays LINEAR.** `config.SPI_GAMMA` (2.1)
  is applied by the firmware's CIE LUT downstream, so the renderer reads back at gamma
  1.0. Correcting gamma on the Pi too would double-correct.
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
`rayglow` package (deployed by `git pull` + editable install); the **RP2350** is flashed
with the firmware (`cargo run` / `probe-rs`, see `firmware/README.md`). This repo (+ git
history + `docs/design-history/`) is the durable shared memory across them — prefer
writing knowledge into tracked files. The sshfs/NFS mount of the Pi (`~/pi-mount/`) is
only a convenience for live-editing shaders. Machine-specific addresses/paths are
generalized to placeholders in tracked files; real values live in the gitignored
`LOCAL-SETUP.md` (template: `LOCAL-SETUP.example.md`).

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
