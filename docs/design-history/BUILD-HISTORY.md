> **Archived as written, 2026-06-07.** A knowledge-export brain-dump from the Pi-side
> build agent, captured just before this repo restructure — so its paths and package
> names refer to the **pre-RayGLow** layout: `rpi-custom/` is now this repo,
> `milk/` → `rayglow/feed` + `rayglow/legacy`, `shadertoy/` → `rayglow/render`,
> `MilkDrop3/project-milk-pi.md` → `docs/design-history/project-milk-pi.md`. The
> *facts* (hardware tuning, EGL quirks, calibration constants, load-bearing oddities)
> are the point and remain accurate; mentally remap the paths. Items marked
> **(uncertain)** are reconstructed from summarized context — verify exact figures.

# BUILD-HISTORY.md

Knowledge export for `rpi-custom/` — the undocumented "why" behind the code.
Captures decisions, dead ends, empirical quirks, calibration, and load-bearing
oddities that are NOT already in code docstrings, the repo `CLAUDE.md`, or
`MilkDrop3/project-milk-pi.md`. Facts and history only.

Things marked **(uncertain)** are reconstructed from summarized memory rather than
direct recall — verify before trusting the exact figure. The *shape* of these claims
is reliable; specific decimal values may have drifted across context compaction.

Scope note: three subsystems live here — the standalone numpy visualizers
(flocking, plasma, navier-stokes, etc.), the **milk** MilkDrop-style renderer, and
the **shadertoy** GPU pipeline. The milk renderer's own architecture is documented
in `MilkDrop3/project-milk-pi.md`; this file deliberately does not repeat it.

---

## 1. Decision log — non-obvious decisions and why

### Hardware

- **`gpio_slowdown=5`, not 4.** With hardware OE pulsing active, slowdown 4 produced
  rare end-of-line data corruption on panel 1 (the first panel, which is both first in
  the data chain and the only one referenced across the Pi↔panel ground boundary).
  Slowdown 5 buys sampling margin at the cost of some refresh duty. This was an
  experimental finding from the `tuner.py` sweep, not a guess.
- **The ground strap is what actually fixed the artifact**, not slowdown alone. A wire
  from a Pi header GND to panel-1's power-GND lug, routed along the ribbon, stiffened
  the voltage reference. Slowdown 5 + ground strap together reduced the artifact to
  "only visible if you go hunting for it." The ribbon's own ground wires were too weak
  a tie on their own.
- **`adafruit-hat-pwm` requires a physical solder mod** (GPIO4↔GPIO18 jumper). Setting
  `disable_hardware_pulsing=0` on plain `adafruit-hat` silently did nothing: OE was on
  GPIO4, and `HardwarePinPulser::CanHandle` only accepts GPIO18/12, so it fell back to
  the software TimerBasedPinPulser with no error. The mod moves OE onto GPIO18 where the
  hardware pulser engages.
- **Panel physically flipped instead of a `Rotate:180` pixel mapper.** The earlier
  `Rotate:180` mapper was retired once the panel was mounted the right way around —
  no mapper is now configured. (uncertain: whether any script still references the old
  mapper.)
- **`pwm_lsb_nanoseconds=130`** is the library default and was deliberately *kept* after
  testing — raising lsb made the panel-1 artifact **worse**, not better (it lengthens
  the lit window during which in-flight bits get corrupted).
- **`pwm_bits=10`, not 11.** (uncertain on the exact reasoning) — 10 was the settled
  value in the tuned config; 11 bits costs refresh rate for a bottom-end gradient
  improvement that wasn't worth it on these panels.

### Software architecture

- **`milk/config.py` is the single source of truth for geometry**, and the shadertoy
  pipeline imports it (`from milk import config`) rather than duplicating the 256×32
  numbers. `CHAIN=4` is the one knob if the removed 5th panel returns.
- **`drop_privileges = 0` for the shadertoy runner** (we keep root). Hot reload re-reads
  the `.glsl` from `` after `RGBMatrix()` is constructed, and `` is
  mode 0700 — the `daemon` user the library drops to cannot traverse it. Since these
  tools already run under `sudo`, staying root is the path of least resistance. See §3
  and §5.
- **Render thread pinned to core 0** (`RENDER_CORE=0` in config), because the hzeller
  GPIO update thread owns core 3. CPU pinning is per-thread on Linux, so this does not
  disturb the matrix thread.
- **Protocol v1 was designed additively, not as a replacement.** When the true sub-bass
  band was added, the receiver was made to accept *both* v0 (556 B) and v1 (564 B); a
  v0 packet reports `sub = bass`. This means the Pi and the desktop sender can be
  upgraded in either order without breaking. The MilkDrop band split (bass/mid/treb)
  was left completely untouched because the `.milk` presets depend on its exact edges.
- **The `milk` iChannel texture was introduced specifically to escape the audio
  texture's dynamic-range clamp.** Shadertoy's audio-texture spectrum is Web-Audio
  dB-mapped and everything above roughly −30 dB clamps to 1.0, so bass reads pinned high
  whenever music played (the user observed this as "binary" behavior; shadertoy.com
  behaves identically). The `milk` texture exposes the sender's auto-gained scalars
  instead, where values above 1.0 survive.
- **Derived signals (d/dt, envelope, integrated phase) were cooked into the `milk`
  texture rather than left to per-shader buffer files**, so most audio-reactive shaders
  need no `.bufA.glsl` at all. The shaped-velocity case (a `BASE_RATE + BOOST` integrator
  like `will-helix.bufA.glsl`) still needs a buffer file because those are artistic knobs
  the pipeline can't guess.
- **GLSL `#version 300 es`** chosen for the preamble: Shadertoy targets WebGL2 = GLSL ES
  3.00, which is the closest dialect match. The Pi's driver is actually ES 3.10 / GLSL ES
  3.10 and compiles the 3.00 source fine.
- **`#define HW_PERFORMANCE 0`** added to the preamble after a user-reported error:
  shaders that did `#if HW_PERFORMANCE == 0` failed to compile because Shadertoy injects
  that macro server-side and it wasn't present locally. 0 = the mobile/low-power branch.

### Tuning sessions

- **The amplitude/theta "always maxed" problem** was traced to the audio-texture dB
  clamp described above, not to the user's smoothstep ranges. The fix was upstream
  (expose auto-gained scalars), not a threshold tweak.
- **The subwoofer-invisibility investigation** (user held a hand to the sub and saw no
  correlation): MilkDrop's "bass" band is linear FFT bins 0–85 ≈ 0–4 kHz, and the
  log-equalize table suppresses the lowest bins heavily. So "bass" tracks low-mids, and
  an actual subwoofer (~25–117 Hz, the lowest ~2 bins) is nearly absent from it. This is
  faithful MilkDrop behavior, not a bug — hence the additive sub band rather than a
  re-tune of the existing split.

---

## 2. Dead ends — tried and abandoned

- **`disable_hardware_pulsing=0` on plain `adafruit-hat`** — appeared to enable hardware
  PWM but silently fell back to software pulsing (OE on the wrong GPIO). Abandoned for
  the solder mod + `adafruit-hat-pwm`. (See §1.)
- **Raising `pwm_lsb_nanoseconds`** to fight the panel-1 artifact — made it worse.
  Reverted to 130.
- **Bulk capacitance as the artifact fix** — a 2200 µF cap was added at panel 1's power
  input during debugging; it made no difference to the artifact (left installed anyway,
  harmless). The artifact was a *signal-integrity / ground-reference* problem, not a
  power-sag problem. Twisting the jumper wire into a tight pair to kill EMI also did
  nothing — ruled EMI out.
- **`Rotate:180` pixel mapper** — worked, but retired in favor of physically flipping
  the panel.
- **BLE as the desktop→Pi transport** — evaluated and rejected (documented in
  `project-milk-pi.md §7`: throughput, latency floor, MTU fragmentation, and the Pi 4's
  shared WiFi/BT antenna). Noted here only so it isn't re-explored.
- **`np.percentile` / lazy numpy imports inside the render loop** — worked headless,
  crashed on hardware. `numpy.ma` is imported lazily on the first `np.percentile()` call;
  if that first call happens after the privilege drop, it dies with
  `ModuleNotFoundError: No module named 'numpy.ma'` because site-packages is under
  `~/venv` which `daemon` can't reach. Hit in `gray-scott.py`. The fix (warm up the
  full path before `RGBMatrix()`) is now standard, but the naive version is a real dead
  end that passes every headless test.
- **Audio-texture spectrum as a dynamic control signal** — worked but rejected: the
  −30 dB clamp makes it binary. Superseded by the `milk` texture. The audio texture is
  still present and useful for *waveform* display and for shaders that genuinely want the
  Shadertoy-shape texture.
- **`will-helix.glsl` reading `texelFetch(..., ivec2(4,0), ...)` against the bufA
  channel** — a partial failure that looked right. The `ivec2(4,0)` sub-texel index
  belongs to the *milk* channel; bufA only ever writes texel (0,0), so against bufA that
  fetch reads black and theta/amp come out zero. (uncertain whether the user has since
  corrected the file.)

---

## 3. Hardware & environment quirks (empirical)

### Panels & power

- **P6-3528 64×32 panels**, 4 daisy-chained in one row = **256×32**. Originally 5; one
  removed and may return — check `CHAIN`/`chain_length` before assuming width.
- **One panel has scattered color-specific dead sub-pixels** (~3 with no green, ~5 with
  no red) — a hardware fault from storage, not software. Don't chase it in code.
- **FM6126A panel-type init is NOT needed** — the default init sequence drives these
  panels correctly.
- **Power: 5V 30A supply → thick copper bus** (one +5V run, one GND run touching every
  panel), entering at panel 1 (also first in the data chain). The Pi is powered
  separately over USB. The bus is *stiffest* at panel 1, which is why positional rail
  sag was ruled out as the artifact cause.
- **`snd_bcm2835` must be blacklisted.** The library hard-exits on `adafruit-hat-pwm` if
  the onboard sound module is loaded (PWM hardware conflict). This is a prerequisite for
  the whole PWM path, not optional.

### The panel-1 artifact (root cause, for posterity)

Random flashing in the last ~8–12 px of mirrored scan rows r / r+16, panel 1 only.
Root cause (uncertain on the exact `framebuffer.cc` line, ~985): `DumpToMatrix` clocks
the next bit-plane's data out *while the previous OE pulse is still lit* (by design);
the OE turn-off transient corrupts the in-flight bits at panel 1's inputs — those being
the last-clocked bits, and panel 1 being the only one straddling the Pi↔panel ground
boundary. Fixed by slowdown 5 (sampling margin) + the ground strap (stiffer reference).
`tuner.py` is the sweep harness built to chase this (stress pattern; flags
`--slowdown --lsb --pwm-bits --dither --brightness --show-refresh --animate`).

### Pi & environment

- **Raspberry Pi 4B (4 GB)** is also the dev box — everything builds and runs on the Pi
  itself, including the agent sessions.
- **Pi IP 192.168.0.50**, IoT VLAN, WiFi, DHCP. Desktop→Pi UDP 5005 crosses VLANs with
  no OPNsense rule needed (verified via ICMP-unreachable probe).
- **The Pi filesystem is mounted on the desktop** at `~/pi-mount/`; the user
  edits/copies files across that mount. The desktop sender lives at
  `desktop:~/Projects/milk-pi/sender.py`; `rpi-custom/sender.py` is the
  reference copy the user pushes back to the desktop and restarts there.
- **Ollama server at 192.168.0.20:11434** (gemma3:latest default) for the `ollama.py`
  LLM-shell experiment.

### EGL / GL driver behavior (Pi 4B, V3D)

- **Headless GL works with no X and no GBM**: ctypes `libEGL.so.1` + `libGLESv2.so.2`,
  surfaceless platform `EGL_PLATFORM_SURFACELESS_MESA = 0x31DD`, via `/dev/dri/renderD128`.
- **`eglChooseConfig` returns 0 configs on the surfaceless platform** — this is expected,
  not an error. The no-config-context path (`EGL_KHR_no_config_context`, pass
  `EGL_NO_CONFIG`) works: create context with `{EGL_CONTEXT_CLIENT_VERSION, 3}`, make
  current with `EGL_NO_SURFACE` for both draw and read, render into an FBO.
- **Driver is V3D 4.2, Mesa 25, OpenGL ES 3.1 / GLSL ES 3.10.**
- **V3D supports `EXT_color_buffer_float` AND float-linear filtering**, so RGBA32F render
  targets can use `GL_LINEAR`. This is why the multipass buffers are RGBA32F (values >1
  survive; needed for the integrator state and Game-of-Life age coloring). The `milk`
  texture is RGBA32F NEAREST for the same survives-above-1.0 reason.
- **GPU rendering and GPIO bit-banging coexist fine** — GPU work on core 0, hzeller
  thread on core 3, no measured interference.
- **ctypes binding hazard**: every EGL/GLES function needs explicit `.argtypes`/`.restype`
  or wrong-width args segfault rather than raise. Error-checking (`eglGetError`/
  `glGetError`) is done at init only, not per-frame.

### The venv

- **`~/venv`** holds numpy, PIL, and the `rgbmatrix` Python binding. Run as root via
  `sudo ~/venv/bin/python ...`.
- **Environments are managed with `uv`** (user preference, 2026-06-01): `uv venv`,
  `uv pip install --python /venv/bin/python <pkg>`.
- **How `rgbmatrix` got installed**: the repo became pip-installable in the Feb 2026
  Python overhaul (`pip install .` from the repo root, scikit-build-core + Cython). The
  binding in the venv comes from that. (uncertain whether `venv` was built with
  `--system-site-packages` or the deps were installed directly.)

---

## 4. Magic numbers & calibration

| Constant | Value | Where | Meaning / what changes if altered |
|---|---|---|---|
| `gpio_slowdown` | 5 | config.py | 4 → rare panel-1 end-of-line corruption; higher → dimmer (less duty). |
| `pwm_lsb_nanoseconds` | 130 | config.py | library default; raising it made the artifact worse. |
| `pwm_bits` | 10 | config.py | 11 costs refresh for marginal low-end gradient. |
| `brightness` | 100 | config.py | full. |
| `GAMMA` | 1.2 | config.py | composite exponent; <1 lifts faint trails, >1 deepens blacks. Repo convention is a direct exponent. |
| `WAVE_FIT` | 1.0 | config.py | `.milk` custom-wave vertical map; 1.0 squashes the ~square preset canvas onto 8:1 (all visible, compressed); 0.0 = MilkDrop-faithful (only center 1/8 shows). |
| `FALLBACK_AFTER` | 0.5 s | config.py | silence before synth fallback engages. |
| `RENDER_CORE` | 0 | config.py | core 3 belongs to the GPIO thread. |
| `--scale` (shadertoy) | 4 default | __main__.py | supersample factor; 1 = pixel-exact. Heavy shaders: ~17 fps @4, ~58 @2, ~257 @1 (raymarchers want 2). example.glsl: 582/132/74/24 fps at scale 1/2/4/8. |
| spectrum dB window | −100..−30 dB → 0..1 | textures.py AudioChannel | Web-Audio range; >−30 dB clamps to 1.0 (the binary-bass cause). |
| analyser smoothing | 0.8 | textures.py | Web-Audio default magnitude smoothing. |
| `DDT_LAG` | 25 /s (~40 ms) | textures.py MilkChannel | derivative slew; faster aliases the packet-vs-frame beat, slower blurs onsets. |
| `ENV_LAG` | 8 /s (~125 ms) | textures.py MilkChannel | envelope chase; mirrors will-helix `AMP_LAG` feel. |
| `THETA_WRAP` | 200π ≈ 628.3185 | textures.py MilkChannel | phase wrap; `sin(theta*k)` is seamless only for `k` a multiple of 0.01. |
| will-helix theta wrap | 20π ≈ 62.8319 | will-helix.bufA.glsl | seamless only for line speeds that are multiples of 0.1 (the image pass uses multiples of 0.1). |
| `BASE_RATE` | 0.4 | will-helix.bufA.glsl | idle helix speed when quiet. |
| `BOOST` | 1.6 | will-helix.bufA.glsl | extra speed at strong bass. |
| `LAG` | 3.0 /s | will-helix.bufA.glsl | how fast v chases target (low = flywheel, high = twitchy). |
| `AMP_QUIET / AMP_LOUD` | 0.38 / 1.0 | will-helix.bufA.glsl | wave amplitude range. |
| `AMP_LAG` | 8.0 /s | will-helix.bufA.glsl | amplitude chase (snappier than v so hits swell without strobing). |

### Sender-side audio analysis (constants in `sender.py`, ported from vis_milk2)

- **`WINDOW = 576`** sample analysis window, left channel, Hann envelope, zero-padded to
  **`NFREQ = 1024`**-pt FFT → 512 magnitude bins. This is MilkDrop-exact (`plugin.h:61`,
  `fft.cpp`).
- **`EQUALIZE = -0.02 * ln((512 - i)/512)`** — the log table that suppresses low bins.
- **Band edges `512*i//6` for i=0..3 → bins 0, 85, 170, 256** (`plugin.cpp:8739`). So
  "bass" = bins 0–85 ≈ 0–4 kHz. This is why a subwoofer is invisible in "bass."
- **AutoGain**: `long_avg` retention 0.9 for the first 50 frames (fast converge) then
  0.992; `avg` attack 0.2 rising / 0.5 falling; `imm_rel = imm/long_avg` (1.0 = typical,
  hits spike 2–4). Ported from `plugin.cpp:8750`.
- **Sub band (v1)**: `SUB_WINDOW = 2048` (42.7 ms @ 48 k → 23.4 Hz/bin), Hann envelope,
  **no equalize**, `SUB_BINS = slice(1, 6)` = bins 1–5 = **23–117 Hz** (bin 0 = DC,
  skipped), its own AutoGain. `SAMPLE_RATE = 48000`.
- **Measured proof of the band problem** (uncertain on exact decimals): a 3 kHz tone
  reads roughly 55× stronger in the "bass" band than a 40 Hz tone; the new sub band reads
  ~1015 at 40 Hz and essentially 0.0 at 1 kHz. Low bins are suppressed ~90× by equalize.

### Packet sizes

- **v0 = 556 bytes**, `<IHHIf7f128f` (magic, ver, flags, seq, t, 6 bands + vol, wave[128]).
- **v1 = 564 bytes**, `<IHHIf7f128f2f` (appends sub, sub_att).
- **Magic = 0x4D494C4B ("MILK")**, UDP port 5005, ~60 Hz, ~33 KB/s at v0.

---

## 5. Load-bearing oddities — looks wrong/redundant, breaks if "fixed"

This is the most important section. Each item looks like cruft or a mistake and is not.

- **`drop_privileges = 0` in the shadertoy runner is intentional, not a security
  oversight.** Reverting it to the default breaks hot reload: the `daemon` user can't
  traverse `` (mode 0700) to re-read the `.glsl`. Everything here runs under
  sudo by necessity (GPIO), so staying root is deliberate.
- **All imports and file loads happen BEFORE `RGBMatrix()` everywhere.** This is not
  stylistic ordering — it's the privilege-drop guard. A lazy import (`numpy.ma` via
  `np.percentile`, PIL plugin loading, any `from . import x` inside a draw path) that
  first fires *after* construction crashes on hardware while passing every headless test.
  The shadertoy runner even does a throwaway `feed.update()` + `toy.render()` + a PIL
  `Image.fromarray()` before constructing the matrix purely to warm those paths. Do not
  "clean up" the warm-up call.
- **`pwm_lsb_nanoseconds=130` is set explicitly to the library default.** It looks
  redundant (it equals the default) but it documents that the value was tested and that
  raising it regresses the panel-1 artifact. (uncertain whether removing it would change
  behavior — it shouldn't, but the comment carries the calibration history.)
- **The 2200 µF cap at panel 1 does nothing for the artifact** and is left installed
  anyway. Don't remove it expecting a change; don't add more caps expecting a fix — the
  artifact was ground-reference, not power.
- **The MilkDrop band split (0/85/170/256) is deliberately "wrong" for sub-bass and must
  stay that way.** The `.milk` presets are tuned against those exact edges; "fixing" the
  bass band to include sub frequencies would change how every preset reacts. The sub band
  was added *alongside* it for this reason.
- **`milk` texture is 8×1 but only 7 texels are used; texel 7 is zero.** Reserved
  headroom, not a bug. Texels 5–6 hold derived signals (phase, packet age, live flag),
  not band scalars — indexing them as if they were a 6th/7th band reads nonsense.
- **`vol` writes the same value to `.x` and `.y`** in the milk texture (imm and att are
  identical for volume). Looks like a copy-paste error; it's intentional so shaders can
  treat every band uniformly (`.x`/`.y` always valid).
- **`milk` texture derived signals start `_env` at 1.0 and skip derivatives on the first
  frame** (`dt == 0`). Seeding env at 0 would make every visual fade up from black on
  start; the first-frame derivative skip avoids a divide-by-zero spike.
- **`will-helix.bufA.glsl` reads bass from `texelFetch(iChannel1, ivec2(4,0), 0).x`
  (the *sub* texel) but amplitude from `ivec2(0,0).y` (bass_att).** Mixing sub for rate
  and bass for amplitude is deliberate tuning, not an inconsistency — see the in-file
  comment about position-like vs size-like signals.
- **The shadertoy preamble defines `main()` itself and forbids the user shader from
  defining one** (regex-detected → clear error). A shader that defines `main` isn't
  broken Shadertoy code; the wrapper just owns `main` and calls the user's `mainImage`.
- **`eglChooseConfig` returning 0 is not checked as an error.** On surfaceless V3D that's
  the normal result; the no-config-context path is taken on purpose. A "fix" that bails on
  0 configs would break all headless rendering.
- **GLSL error line numbers are remapped** by subtracting the preamble line count. If the
  preamble grows (e.g. the `HW_PERFORMANCE` define was added), `PREAMBLE_LINES` must track
  it or every reported error line is off. This coupling is invisible until it's wrong.
- **`milk-verbose.glsl` / `milkfeed.glsl` convert `gl_FragCoord` to panel pixels via
  `floor(I / iResolution.xy * PANEL)`.** A naive 1-px bar at `--scale 4` would render at
  quarter brightness (it'd cover 1 of 4 supersamples). The conversion is required for any
  pixel-exact UI element, not decoration.
- **Channel directives live in `.glsl` comments** (`// iChannelN: spec`), not just CLI
  flags, *because Shadertoy stores bindings in site metadata, not the shader source.*
  Porting a multipass shader without them silently binds the wrong/black textures.
- **The receiver keeps only the highest-`seq` packet per frame and never blocks.** During
  testing, a hand-crafted test packet "lost" to the live desktop stream — that was the
  latest-wins logic working correctly, not a bug. Don't add blocking reads to "fix"
  dropped test packets.
- **`fake_sender.py` is kept even though the real desktop sender supersedes it.** It's the
  music-free local test harness AND an executable spec of the packet format — it was
  updated to v1 in lockstep so it doubles as the protocol reference.

---

## 6. Unfinished threads

- **The desktop sender must be redeployed for v1 to take effect live.** Until
  `rpi-custom/sender.py` is copied to `desktop:~/Projects/milk-pi/sender.py`
  and restarted, the live stream is v0 and `sub` falls back to `bass`. The Pi side already
  accepts both, so order is safe. (uncertain: whether the user has now done this — they
  reported "reconfiguring of the sender.py ran without issue," which suggests yes.)
- **`will-helix.glsl` image-pass channel index** may still read `ivec2(4,0)` against bufA
  (always black). Suspected the `ivec2(4,0)` edit landed in both the image and buffer
  files when it belonged only in the buffer file. (uncertain if corrected.)
- **`milk` texture texel 7 is reserved and unused** — left open for a future signal
  (CPU temp/usage was discussed as a candidate and explicitly NOT added, since nothing
  consumes it yet and `/sys` temp / `/proc/stat` are second-scale reads, not 60 Hz).
- **Optional flicker-reduction steps never taken** (rig is "good enough"): `isolcpus=3`
  core isolation via cmdline.txt (`isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3
  irqaffinity=0,1,2`), the repo's `optimized-kernel/` prebuilt kernel, and re-testing
  `slowdown=4` now that the ground strap exists (`tuner.py` makes it a 1-minute test).
- **Per-line phases for will-helix** (10 independent state texels so each helix line has
  its own phase) — suggested, not built.
- **64-bar per-bin FFT variant of milkfeed** — suggested, not built.
- **Latency hardening shelved** — WiFi power-save off, or a point-to-point Ethernet link
  to the desktop's idle X540 NIC (full recipe in `project-milk-pi.md §7`).

---

## 7. Timeline sketch (approximate; uncertain on exact dates)

- **~2026-02 (Feb):** repo Python overhaul — whole repo becomes pip-installable
  (scikit-build-core + Cython). `rgbmatrix` ends up in `~/venv` from this.
- **~2026-06-01:** user adopts `uv` for venv management.
- **Early June 2026:** hardware artifact hunt — PWM solder mod, `snd_bcm2835` blacklist,
  `tuner.py` sweeps, the elimination chain (EMI / bulk cap / rail sag all ruled out),
  ground strap installed, settled on slowdown 5 + lsb 130. Rig declared stable.
  Standalone numpy visualizers built across this period (flocking, plasma,
  navier-stokes/LBM, phase-flow, fields, gray-scott, ollama shell, etc.).
- **June 2026 — Milk-Pi:** MilkDrop reverse-engineered on the desktop (Wine), packet
  protocol v0 defined, Pi renderer + fake_sender built and verified headless, then the
  `.milk` dotmilk subpackage (parser/eel/runtime/waves/custom/triage), 311 presets
  parsed, ~142 keepers, run on hardware with the live desktop sender — "working pretty
  much flawlessly." Project considered shipped.
- **June 2026 — shadertoy pipeline:** v1 single-pass + hot reload (hardware-verified
  side-by-side vs browser, "high fidelity"); v2 iChannel textures + audio (the
  `HW_PERFORMANCE` fix landed here after a user report); v3 multipass Buffer A–D
  (Game-of-Life and A→B chain ordering verified headless). User curates a ~40-shader
  `presets/` collection.
- **June 2026 — audio dynamic-range work:** diagnosed binary bass (dB clamp) → added the
  `milk` channel; built `will-helix` forward-Euler integrator (bufA) as a teaching
  example of velocity-vs-position; diagnosed subwoofer invisibility (band edges +
  equalize) → protocol v1 with a true 23–117 Hz sub band across sender/receiver/features/
  fake_sender/textures; built `milkfeed.glsl` (5-bar live diagnostic) and `milk-verbose.glsl`
  (every-float reference card); extended the `milk` texture with d/dt, envelope, integrated
  phase, packet age, and live flag.
- **2026-06-07:** this knowledge export written, ahead of a planned repo restructure.
```
