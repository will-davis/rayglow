# REMOTE-RENDER-PLAN

> **Status: tentative.** Written 2026-08-02 as a handoff for a future session. Nothing
> here is built yet. Supersedes nothing; if adopted, the code lands in **`~/Projects/rayglow`**
> (this repo gets zero gateware changes — see §3).
>
> **Updated 2026-08-02 (same day, later):** the 120 Hz DPI upgrade this plan warned
> against in §5.3 shipped independently and is confirmed clean on the wall (contract
> v0.2, skip-gated double buffer, 122.14 Hz production). §5.3 is rewritten from "don't"
> to "target it, pinned to SW5=6"; all rates, the latency budget, and the credit numbers
> are updated to the 122 Hz baseline. Also corrected: the Pi sink is `drm_out` (dumb
> buffers + page flip), not `/dev/fb0` — fbdev emulation was retired 2026-07-29.

Move GLSL rendering off the Pi 5 and onto `ubuntu-server`'s RTX 4080, keeping the Pi as a
vblank-paced DPI framebuffer and leaving the ECP5 translation layer completely untouched.

## 1. Why

The Pi 5's VideoCore VII is **51.2 GFLOPS FP32**. At 384×128 with `scale=2` (768×256 =
196,608 px) at 60 Hz that is ~4,340 FLOP/px of theoretical peak, and realistically
~1,300 FLOP/px sustained — and the 2026-08-02 move to a 122.14 Hz source *halves* those
budgets again (~650 FLOP/px sustained). A moderate Shadertoy raymarcher — 64–128 march
steps, 4–6 extra SDF evals for normals, a soft-shadow march — costs **5,000–50,000
FLOP/px**. The wall is short by roughly 8–80× on exactly the shaders worth showing.

The RTX 4080 is **~48 TFLOPS FP32, ~950×** the Pi. Frigate uses ~20%, leaving ~38 TFLOPS
against a workload needing ~0.02. The GPU is not the constraint after this change; nothing
is.

**The counter-intuitive result (§5): this architecture is *lower* latency than today**,
because the render stage currently dominates the budget and collapses from 25–50 ms to
~1 ms, while the added network hop costs ~1.7 ms.

## 2. Target architecture

```
will-desktop (192.168.1.105, VLAN 10)          ubuntu-server (192.168.1.101, VLAN 10)
┌────────────────────────────────┐             ┌──────────────────────────────────────┐
│ music ▶ PipeWire sink monitor  │   UDP:5005  │ feed.receiver (latest-win)           │
│ sender.py: FFT ▶ bands         │ ──────────▶ │ render: GLSL ▶ EGL device platform   │
│ + flywheels/beat/key @ ~60 Hz  │  ~180 KB/s  │ RTX 4080, headless, no X             │
└────────────────────────────────┘             └──────────────────┬───────────────────┘
                                                                  │ UDP, 147 KB/frame
                                                                  │ ~17 jumbo datagrams
                                          credit token (1 dgram)  │ ≈144 Mbit/s @ 122 Hz
                                          ◀───────────────────────┤
                                                                  ▼
                                               rpi5 (moving to VLAN 10 — see §7)
                                               ┌──────────────────────────────────────┐
                                               │ framesink: reassemble ▶ page-flip    │
                                               │ ▶ drm_out dumb bufs (NO GL, NO EGL)  │
                                               └──────────────────┬───────────────────┘
                                                                  │ DPI, 25 MHz, 122.14 Hz
                                                                  ▼
                                               ECP5-EVN — UNCHANGED (INTERFACE-CONTRACT v0.2)
                                               ▶ 4 chains ▶ 384×128 @ 171.2 Hz refresh (SW5=6)
```

Roles: **desktop = audio**, **ubuntu-server = pixels**, **Pi = timing**, **FPGA = panels**.

## 3. What changes, and what emphatically does not

| Component | Change |
|---|---|
| `rayglow-fpga` gateware | **None.** Not one line. |
| DPI signalling / modeline | **None.** As-built v0.2: 25 MHz, 122.14 Hz, same 384×480 clamp, same crop. |
| INTERFACE-CONTRACT.md | Doc-only clarification (§4) — the contract is host-agnostic. |
| `sender/sender.py` | **None.** One env var: `RAYGLOW_HOST` → `192.168.1.101`. |
| `rayglow/render/egl.py` | **Add** an NVIDIA device-platform path alongside the Mesa one. |
| `rayglow/render/` output | **Add** a network frame sink. `--output kms` stays as fallback. |
| Pi software | **New**, small: a receiver + `KmsOut`. Drops the GL stack entirely. |

That the gateware is untouched is not a happy accident — it is the interface boundary
doing its job. The FPGA was specified as *a monitor*, and a monitor does not care which
machine is driving it. This is the strongest available evidence the boundary was drawn in
the right place.

**The Pi gets simpler, not more complex.** It no longer needs Mesa, EGL, GLES, or the
shader pipeline — just a socket and a memcpy. `drm_out` already takes a plain
`(H, W, 3) uint8` array and owns everything hard (dumb-buffer allocation, hardware
page flip, event pacing, XRGB8888 swizzle, stride, clipping), so the receiver is ~150
lines against an already-proven sink. (Not `/dev/fb0`/`KmsOut` — fbdev emulation was
retired 2026-07-29 for tearing through cached shadow copies.)

## 4. Git strategy

**No forks. One feature branch, in `rayglow` only.**

A fork solves a *permissions* problem — you don't have one, you own both repos. What a
fork would buy you is a second remote to keep in sync forever, in exchange for nothing.
Forking *both* repos would be worse still: it doubles the sync burden and manufactures a
cross-repo version-matching problem, which is precisely what INTERFACE-CONTRACT.md exists
to prevent. And since `rayglow-fpga` receives zero code changes, a fork of it would be
pure liability.

The change is **additive and flag-guarded**, which is the pattern this project already
prescribes and has already executed once: `--output kms` was added alongside `--output
wall` without disturbing the RP2350 path, and `--transport spi` survives as the proven
fallback. Do the same thing again.

```
rayglow:      feat/remote-render     # all work happens here
rayglow-fpga: (no branch)            # doc-only, direct small commit when proven
```

**Branch plan**
- Small commits at checkpoints; each phase in §6 is a natural commit boundary.
- Merge to `main` only once the wall runs on it *and* `--output kms` still works —
  the local-render path is the fallback and must not rot.
- `rayglow-fpga`: when proven, one commit — a ROADMAP status line plus an
  INTERFACE-CONTRACT bump to **v0.3** (v0.2 was taken 2026-08-02 by the 120 Hz
  ratification) stating explicitly that the contract specifies DPI signals and timing
  and says nothing about which machine generates them. Small, correct, and it
  formalizes why no gateware work was needed.

**Deployment.** The repo already handles multi-machine roles (`sender/` is its own uv
project; `rayglow/` installs editable on the Pi). A third role is a *deployment* concern,
not a repo-structure one. Add a third mutagen session:

| Session | Alpha (desktop) | Beta |
|---|---|---|
| `rayglow-code` | `~/Projects/rayglow` | `rpi5:/home/will/rayglow` *(existing)* |
| `rayglow-shaders` | `~/Projects/rayglow-shaders` | `rpi5:/home/will/presets` *(existing)* |
| **`rayglow-render`** | `~/Projects/rayglow` | `ubuntu-server:/home/will/rayglow` *(new)* |
| **`rayglow-shaders-render`** | `~/Projects/rayglow-shaders` | `ubuntu-server:/home/will/presets` *(new)* |

The desktop stays the single source of truth — "edits here ARE the deploy" still holds,
now fanning out to two targets. Hot-reload keeps working; the watcher just runs on
ubuntu-server.

## 5. Latency analysis

The concern is legitimate and worth the arithmetic. The conclusion is that **the new
network hop is ~3% of the budget, and the change is a net latency *win*.**

### 5.1 Budget, 122.14 Hz DPI / SW5=6 (recommended config — see §5.3)

**Audio front-end — unchanged by this project, and it dominates:**

| Stage | Latency | Note |
|---|---|---|
| PipeWire capture | ~5–11 ms | `blocksize=256, latency="low"` = 5.3 ms/quantum |
| FFT window group delay | **6 / 21 / 43 ms** | Hann centroid = window/2 (see 5.2) |
| Sender frame quantization (~60 Hz) | 0–17 ms, avg 8 | |
| **Subtotal** | **~19–36 ms typical** | up to ~60 ms if driven by `spec[]` |

**Transport + render — what this project changes:**

| Stage | Latency | Note |
|---|---|---|
| Feature UDP, desktop→ubuntu-server | ~0.2 ms | ~3 KB, one datagram, same VLAN, switched |
| **Render (RTX 4080)** | **~0.5–2 ms** | well inside the 8.19 ms/frame cadence budget |
| Frame serialize + UDP TX, 147 KB @ 1 GbE | ~1.4 ms | 1.18 ms wire + switch + stack |
| Pi reassemble | ~0.3 ms | ~17 jumbo datagrams |
| **Subtotal (this is the "new" cost)** | **~2.4–4 ms** | |

**Display pipeline — halved by the 120 Hz upgrade:**

| Stage | Latency | Note |
|---|---|---|
| Pi flip wait | 0–8.2, avg 4.1 ms | pacing; unavoidable at 122.14 Hz |
| Blit into dumb buffer | ~0.15 ms | 196 KB memcpy |
| DPI frame + FPGA capture/adopt | ~8.2 ms | one source frame (capture done 2.1 ms in) |
| HUB75 BCM refresh @ 171.2 Hz | 0–5.8, avg 2.9 ms | SW5=6 |
| **Subtotal** | **~15 ms avg** | was ~29 ms at 60 Hz DPI |

**Total ≈ 37–55 ms typical.**

### 5.2 The dominant term is the FFT, and you can choose it per shader

A Hann-windowed FFT's group delay is the window centroid — half the window:

| Window | Duration | Group delay | Feeds |
|---|---|---|---|
| 576 (→1024-pt) | 12.0 ms | **6.0 ms** | `bass`/`mid`/`treb`, bands **b4–b7** |
| 2048 | 42.7 ms | **21.3 ms** | `sub`, `sub_att` |
| 4096 | 85.3 ms | **42.7 ms** | `spec[128]`, `chroma[12]`, bands **b0–b3** |

This is time-frequency uncertainty, not an engineering defect: you cannot resolve 11.7 Hz
bins without observing 85 ms of signal. But it is *selectable*, and that is free latency:

> **Shader-authoring guideline: drive fast motion from b4–b7 / `bass`/`mid`/`treb`
> (6 ms), and reserve `spec[]`, `chroma[]`, and b0–b3 (43 ms) for slow or ambient
> parameters — color washes, background drift.** A shader whose transients ride the
> 576-window bands is ~37 ms tighter than one riding `spec[]`, for zero cost.

One 122 Hz note: the sender still emits features at ~60 Hz while frames render at
122.14 fps — the flywheels/interpolation already smooth this, so it is *not* a blocker.
An optional later bump of the sender loop to ~120 Hz (same schema, just rate — Will has
okayed schema changes if ever needed) tightens transient quantization by up to ~8 ms.

### 5.3 ✔ RESOLVED 2026-08-02: 120 Hz shipped first and holds — target it, pinned to SW5=6

This section originally argued against bundling the 120 Hz upgrade (cadence-ratio
regression, a belief that ≥240 Hz refresh was prerequisite, RP1 clock-instability
reports near 25 MHz). **The upgrade shipped the same day as an independent experiment
and is confirmed clean end to end on the wall** (contract v0.2, `exp/120hz-dpi` merged).
For the record, why the original analysis was wrong:

- The 1-vs-2-dwell unevenness is real arithmetic but perceptually subordinate: doubling
  the source rate **halves the per-frame motion displacement**, which shrinks the
  straddle/interleave artifact — the very artifact the cadence fear was about — by the
  same factor. Measured by eye on the wall: markedly better motion, no judder. The ≥2×
  cadence rule of thumb did not survive contact with a 122 Hz source.
- The failure mode this section *didn't* know about — the double-buffer handoff budget
  (`scan_frame < DPI_period − capture_time`, violated at 122 Hz) — was fixed
  structurally, not raced: the writer now **skips** a source frame rather than write
  into a buffer the reader still displays (skip-gated CDC, gateware v0.2). Tearing is
  impossible at any timing ratio; an overloaded scan degrades to dropped frames,
  visible on EVN LED **D8**.
- The RP1 25–27 MHz instability did not bite on the wall's kernel: production runs
  `clock-frequency=25000000` with the capture-integrity latches (D10/D11) dark.

**Config to pin for remote render:**

| Setting | Value | Why |
|---|---|---|
| DPI mode | 25 MHz / 122.14 Hz (as-built) | production since 2026-08-02; nothing to change |
| SW5 | **6** (positions 2+3 ON) → 171.2 Hz scan | above the 165.1 Hz zero-drop threshold: **every rendered frame displays**; 91 % brightness (accepted trade) |
| Render pace | credit-paced ⇒ 122.14 fps | the Pi's flip event is the master clock; no separate cap needed |

At SW5=8 (full brightness, 140.4 Hz) the FPGA skip-gate drops ~15 % of source frames —
visually harmless, but with remote render it wastes 15 % of GPU + network work on frames
nobody sees and reintroduces cadence unevenness. No reason to pay that. If full
brightness ever becomes mandatory, the zero-drop route is a ~100 Hz modeline
(`clock-frequency=20500000`) at SW5=8 — not SW5=8 at 122 Hz. Preference order:
**u=6 @ 122.14 > u=8 @ 100 > u=8 @ 122**. Full table: `hardware/SWITCHES-AND-LEDS.md`.

### 5.4 Comparison with today

| | Today (heavy shader, 122 Hz DPI, Pi render) | Proposed |
|---|---|---|
| Audio front-end | 19–36 ms | 19–36 ms |
| Render | **25–50 ms** (heavy shaders can't hold 122 fps at all) | **0.5–2 ms** |
| Network (frames) | — | 1.7 ms |
| Display pipeline | 15 ms | 15 ms |
| **Total** | **~59–101 ms** | **~37–55 ms** |

**Net: 20–45 ms faster** — and, more importantly, heavy shaders become *possible* at
the full 122 fps cadence, which the Pi cannot do at any latency. Even for a trivial
shader that already hits the cap on the Pi, the new path is no worse — the extra
1.7 ms of wire disappears into the flip wait that was already being spent.

### 5.5 ⚠ The real risk is queueing, not transit

Transit time is fine. **Unbounded buffering is the trap that will actually bite.** A naive
`while True: render(); send(frame)` on a 4080 will produce 500+ fps into a link consuming
60, socket buffers fill, and end-to-end latency grows without bound — classic bufferbloat.
It will look like "the visuals drift further behind the music the longer it runs."

**Mitigation — credit-based flow control, mirroring the RP2350 READY handshake already
built for the PIO link:**

- The Pi's blit loop is already paced by `drm_out`'s page-flip completion event. After
  each flip it emits a one-datagram credit.
- The render host holds N credits and blocks when out.
- **The Pi's flip event becomes the master clock for the entire pipeline.** At most N
  frames in flight; latency bounded at N × 8.2 ms.
- Start at **N=2** (one frame of slack absorbs network jitter), try N=1 once stable.

This also stops the renderer free-running at 500 fps, which would burn GPU and heat the
box running Frigate for frames nobody sees. Credit pacing at 122.14 Hz also means the
renderer naturally produces exactly the rate the wall consumes — with SW5=6 (§5.3)
every one of those frames reaches the panels.

**Transport: UDP, not TCP.** A single lost packet under TCP stalls the whole stream for a
retransmit timeout (Linux RTO min ~200 ms) — catastrophic for a visualizer. Under UDP a
lost packet costs one frame; the Pi re-displays the last good one and it is invisible at
122 Hz. This is the same reasoning already documented for the audio feed: *"a lost or late
packet just means the Pi renders with the previous values."* Use a per-frame sequence
number + fragment index, drop incomplete frames, latest-wins.

**Enable jumbo frames** (MTU 9000 on the Pi, the VM vNIC, and the 3850): 147 KB becomes
~17 datagrams instead of ~100, cutting per-packet overhead and interrupt load on the Pi.

### 5.6 Four independent clocks

Desktop audio (48 kHz DAC), ubuntu-server render pacing, Pi DPI vblank (RP1 PLL), and
FPGA refresh (its own PLL off the 12 MHz FTDI reference) are **four free-running clocks**.
They will drift. Every stage must be latest-wins or credit-paced so drift produces a
repeated or dropped frame, never a growing queue. The FPGA's skip-gated handoff
(gateware v0.2) now makes the DPI↔scan boundary drop-safe *by construction* — the same
philosophy this section demands of every other stage. Don't chase this as a bug when it
shows up as an occasional duplicated frame; LED D8 tells you when the FPGA is the one
dropping (it should stay dark at SW5=6).

### 5.7 The compensation you already have

`beat.py` is a **predictive** tracker — `beat_phase` is described as an *"anticipatory
0→1 ramp hitting 1.0 ON the predicted beat."* That is a latency-compensation mechanism
already in the codebase. Adding a signed constant offset lets beat-locked content render
*ahead* of the audio by the measured pipeline depth, driving effective sync toward zero
regardless of the ~60 ms transport budget.

**But measure the net offset, don't minimize the visual path blindly.** The audio you
*hear* is also delayed — if monitoring through the Denon AVR (192.168.2.130), AV receivers
commonly add 30–90 ms of DSP latency, which can exceed the entire visual pipeline. In that
case the visuals are already running *ahead* and want delaying, not advancing.

> **Expose a signed `--latency-comp <ms>` knob, tune it by eye against the actual
> listening setup, and record per-output-path values.** This is worth more than any
> further micro-optimization of the transport.

## 6. Work breakdown

Each phase is independently testable and a natural commit boundary.

### Phase 0 — Headless EGL on NVIDIA *(blocker; do first, it de-risks everything)*
`rayglow/render/egl.py:304` hardcodes `EGL_PLATFORM_SURFACELESS_MESA` (0x31DD), a
Mesa-only platform NVIDIA's proprietary driver does not implement. Add an
`EGL_PLATFORM_DEVICE_EXT` (0x313F) path via `eglQueryDevicesEXT` +
`eglGetPlatformDisplayEXT`, selected at runtime with the Mesa path as fallback.

- Keep **GLES3, not desktop GL 4.6** — costs nothing on NVIDIA and preserves Mesa/Pi
  dry-runs, which is worth more than any GL 4.x feature here.
- Needs `libEGL.so.1` + `libGLESv2.so.2` on the ubuntu-server *host* (libglvnd + driver);
  Frigate's Docker/NVIDIA-container-toolkit setup does not put them there by itself.
- **Accept:** `python -m rayglow.render <shader> --dry-run 120 --no-listen` produces a
  correct GIF on ubuntu-server with no X/Wayland running, and `GL_RENDERER` reports the
  4080.

### Phase 1 — Audio feed retarget
Set `RAYGLOW_HOST=192.168.1.101` on the desktop. No code change.
- **Accept:** `milk-verbose.glsl` dry-run on ubuntu-server reacts to music playing on the
  desktop; all feature bars move.

### Phase 2 — Frame transport
New network sink on the render host; new `rayglow.framesink` module on the Pi (socket +
`drm_out`, no GL). UDP, jumbo frames, seq + fragment index, drop incomplete, latest-wins.
Credit-based flow control with N=2.
- **Accept:** wall renders a known shader from ubuntu-server at 122.14 fps. `--output
  kms` still works on the Pi. Sustained run shows **no latency growth over 30 min** (the
  bufferbloat test — log observed frame age, it must be flat, not climbing) **and EVN
  LED D8 stays dark throughout** (SW5=6 — proof the FPGA consumed every frame too).

### Phase 3 — Measurement and tuning
Instrument a stats line matching the existing convention (`fps render net wait`). Measure
real end-to-end latency (phone slow-mo of a percussive hit vs. the wall is sufficient and
honest). Tune `--latency-comp`.
- **Accept:** measured end-to-end within ~2× of the §5.1 budget; if not, the budget is
  wrong and gets revised here.

### Phase 4 — Reclaim the headroom
With ~950× the GPU, revisit what was previously unaffordable: `scale` back up, heavier
multipass, shaders that were shelved as too slow.
- **Accept:** at least one previously-unrunnable shader running at the full 122.14 fps
  credit cadence.

### Phase 5 — PoE consolidation *(independent; can happen any time)*
See §7.

## 7. Infrastructure notes

**VLAN — resolved.** The Pi currently sits on `192.168.2.113` (IoT VLAN 20) only because
VLAN 10 was reserved for wired devices. Moving it to the unused wired port in the display
room puts it on VLAN 10 alongside ubuntu-server, so the ~144 Mbit/s frame stream (the
122.14 Hz production rate) stays L2
on the 3850's switch fabric and never hairpins through the OPNsense VM. **This must happen
before Phase 2** — do not benchmark the transport across the VLAN boundary and draw
conclusions from it. (Will 2026-08-02: confirmed — all devices will be wired on VLAN 10.)

**PoE.** The room's second port can carry both power and data. The switch is not the
limit: WS-C3850-24XU-S is **60 W/port UPOE, 580 W system budget**; nine Reolinks at ~6 W
leave ~500 W spare. Load is ~30 W (headless Pi 5 ~8–12 W, ECP5-EVN at 12 V ~1–1.5 A).

- **A PoE HAT is impossible** — the 40-pin header is fully occupied by the DPI ribbon to
  JP8, mechanically before electrically. Power must arrive by USB-C.
- Topology: one UPOE run → 12 V splitter at the wall → 12 V direct to the EVN barrel, plus
  a 12 V→5 V **synchronous** buck → USB-C to the Pi.
- **Do not use the LM2596 stock for the Pi** — non-synchronous, 3 A ceiling, ~75%
  efficient, poor transient response, against a load with sharp current steps.
- Cisco UPOE is pre-802.3bt 4-pair 60 W; a generic 802.3bt Type 3 splitter may negotiate
  down to 25.5 W if LLDP doesn't line up, which is tight against 30 W. Either measure what
  a bt splitter actually delivers, or use two guaranteed 802.3at splitters (12 V for the
  FPGA, 5 V USB-C for the Pi) at the cost of a second drop.
- **On the historical undervolt:** fatter all-copper cable fixes IR drop, which was
  probably not the root cause. The Pi was on the same 5 V rail as a 24-panel HUB75 wall —
  a violently pulsed load (BCM switching at 140 Hz, tens of amps at fast edges) that dips
  the rail and moves the shared ground reference. No amount of copper fixes sharing a rail
  with that. PoE fixes it correctly because PoE is **transformer-isolated (1500 Vrms per
  spec)**: the Pi gets an independent supply and its own ground reference, and grounds bond
  at exactly one controlled point — which is what the star scheme in
  `rayglow/hardware/POWER-AND-GROUNDING.md` already does.

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Unbounded queueing → drifting latency | **High** | Credit-based flow control (§5.5); test explicitly in Phase 2 |
| NVIDIA EGL device platform doesn't come up headless | Medium | Phase 0 is first precisely so this fails cheap |
| Pathological shader hangs the GPU → driver reset kills Frigate's TensorRT context | Medium | Bound every march loop with a constant iteration cap. Compute sharing is safe; *fault* sharing is not. This is the one honest argument for a dedicated render box instead of the camera server. |
| SW5 left at 8 → FPGA drops ~15 % of frames (wasted GPU + network, uneven cadence) | Low | Pin SW5=6 (§5.3); D8 lit is the tell-tale |
| Wall now depends on ubuntu-server being up | Low | `--output kms` local render stays as fallback — keep it working |
| Jumbo frames misconfigured on one hop → silent fragmentation | Low | Verify with `ping -M do -s 8972` end to end |

## 9. Open questions

1. Where should the frame protocol live — extend `rayglow/feed/` (which already owns
   packet framing and latest-wins semantics), or a new `rayglow/link/`? The feed module's
   philosophy transfers cleanly; the payload size does not.
2. Should the Pi's `framesink` be a separate console entry point, or `--output kms
   --source net` on the existing module? The Pi no longer needs the GL stack at all, which
   argues for a separate, dependency-light module.
3. Does the control plane (TCP :5006 — shader switch/push, media controls) move to
   ubuntu-server with the renderer? Almost certainly yes, since it controls rendering. Confirm
   nothing on the Pi side depends on it.
4. Is `scale=2` still the right default with ~950× the GPU, or is the LED wall's real
   limit elsewhere (panel pitch, BCM depth)? Phase 4 should answer empirically rather than
   assuming more supersampling is better.

## 10. Prior art in this project

Three patterns here already exist and should be reused rather than reinvented:

- **Credit/READY flow control** — the RP2350 PIO link self-paces off a READY line.
  Same idea, different wire.
- **Latest-wins packet semantics** — the audio feed already drops late packets rather than
  queueing them. Same idea, bigger payload.
- **Additive flag-guarded transports** — `--transport spi` survived the PIO bus and
  `--output wall` survived `--output kms`. Same idea, third instance.
