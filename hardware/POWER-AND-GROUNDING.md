# RayGLow power & grounding — bench findings

Derived empirically on **2026-06-28/29** with a PicoScope 2204A while characterizing
whether the Pi 5 + RP2350 could share the panel 5 V rail. The short version: the
"PSU sag" was mostly **ground bounce in the measurement** + **wire IR drop**, not the
supply collapsing — and the level-shifter logic must be powered **ratiometrically**
from the panel rail, not from a clean rail.

## TL;DR rules (do these)

1. **Single-point star ground at the PSU (−) terminal.** Every return — each panel
   channel's GND and the logic/SBC GND — lands as its *own* conductor on the *same*
   lug. Never daisy-chain logic ground off panel ground. The only other GND bridge is
   the HAT plane ↔ panel via the HUB75 ribbon, which must carry **signal return only**.
2. **Power the '245 buffer VCC from the *panel* 5 V (ratiometric), not a clean rail.**
   The panels compare incoming HIGH against *their own* sagging VCC/GND. If the buffers
   sit at 5.0 V while the panel rail is at 4.1 V, the data lines are ~0.9 V above panel
   VCC → input protection diodes conduct (clamp/latch-up risk). Tracking the panel rail
   keeps HIGH ≤ panel VCC and the logic margin ratiometric. **⚠️ Reconcile with
   [`NET-SPEC.md`](NET-SPEC.md)** — its "dedicated 5 V rail for the buffers" is only
   correct if that rail is sourced from the **panel-side** 5 V at the panel reference.
3. **Heavy copper for the high-current path; no CCA.** Beyond resistance, aluminum
   oxidizes at crimp/screw terminations (Al₂O₃ is insulating) → connection resistance
   grows into hot spots.
4. **Local bulk decoupling** at the HAT/buffer 5 V node knocks down the fast component.
5. **Cap global brightness** — cheapest single lever; cuts peak current → less drop,
   less heat, less bounce, all at once.

## What was measured

Scope on the 5 V entering the control box, ground clip on the chassis (logic GND).
Stimulus = full-white shader flashing, sweeping 0.1 → 100 → 0.1 Hz, ~50 k samples.

| Capture | Result |
|---|---|
| DC-coupled, full sweep | V_min **4.715 V**, V_max **5.249 V**; Pk-Pk max 0.356 / avg 0.161 V |
| AC-coupled (DC blocked) | swing −0.219 … +0.214 V (smaller than DC — AC coupling's ~1 Hz HPF filtered the slow 0.1 Hz droops; **trust DC for absolutes**) |
| Row-population test | ~**5 mV/row** drop on average V, ~**14.5 mV/row** on the minimum → full white ≈ 4.7 V avg, **4.1 V min** |

The 4.1 V min hard-browned-out the Pi 5 (it rebooted). Bumping the PSU setpoint +200 mV
just slid the whole window up — the *swing* (load regulation) is unchanged, and over-
trimming pushes the light-load V + release overshoot toward the RP2350's 5.5 V ceiling.

## The key insight: it was ground bounce, not rail sag

**Smoking gun:** moving the SBCs to a genuinely independent 150 W 5 V supply produced
**almost the identical droop**. That's physically impossible for a real rail sag — an
independent supply can't be loaded by the other's current. The only way both
measurements show the same droop is if the droop lives in the **shared GND reference**.

Mechanism — **common-impedance coupling**:
- On a HUB75 panel, logic and LEDs share **one ground** (not isolated). Tens of amps of
  LED return current flow through the ground network.
- Voltage is a *difference*. The scope (−) sat on a GND node carrying that LED return
  current, so it measured `I_LED · R_ground` (ground heaving), not the 5 V rail moving.
- Root cause: the "star" had a **shared GND segment** between LED return and the logic
  reference. LED current's IR drop landed in the logic GND.

LED return current is supposed to flow through the panel **power lugs** (fat), not the
**ribbon GND** (signal reference, mA). If it's bouncing logic, the heavy return is
inadequate/too thin/not landed at the star, so current diverts through ribbon → HAT →
logic GND. Fix = give the amps a fat, dedicated road to the star; the ribbon GND then
stays quiet. You can't *isolate* the node (the panels need the common reference;
isolation would need digital isolators on every line) — you *redirect the current*.

## Wire gauge math (current path)

`V = I · R_loop`, and R_loop counts **both** conductors (current makes a round trip).
CCA ≈ **1.6×** the resistance of same-gauge copper (≈61–64 % IACS), so the current
**14 AWG CCA panel feed behaves like ~16 AWG solid copper**. Gauge (14→22 = 6.4× area)
dominates; material is the supporting actor.

| Feed | Wire | Loop R | Drop |
|---|---|---|---|
| Panel (per 20 A channel) | 14 AWG CCA, 4 ft pair | ~32 mΩ | 20 A → **0.65 V**; 40 A → 1.3 V; 60 A → 1.9 V |
| SBC | 22 AWG Cu, 6 ft pair | ~194 mΩ | 3 A → **0.58 V**; 5 A (Pi 5 peak) → **0.97 V** |

> ⚠️ 14 AWG (CCA worse) is rated ~15 A for power wiring. 20 A/channel through it runs
> **hot** — a real thermal concern, not just lossy. Upsize to heavy copper (10 AWG+) or
> add more injection points. The 22 AWG SBC feed is an *independent* brownout source —
> a Pi 5 at peak drops ~1 V in the wire alone; go 18–16 AWG Cu or shorten.

Re-measure at the PSU terminals vs. the panel end to see how much "sag" is just wire.

## The PSU's 3 channels are NOT independent

The 5 V / 300 W brick exposes **3 × 20 A** output channels: ch1 → 4 panels, ch2 → 4
panels, ch3 → SBCs, all GND-starred at the PSU. But those channels are the **same
internal rail** brought out to three terminal pairs (paralleled, rated 20 A for the
*connector*), **not** three regulators. So:
- They isolate **wiring** (dedicated conductor pairs = good, effectively star) ...
- ... but **not source voltage** — load-regulation sag is common to all three. The SBCs
  on ch3 still ride whatever the internal rail does under the panels' load.
- This is why a *genuinely separate* supply (or HV-distribution, below) is the only way
  to isolate the SBCs from rail regulation, vs. just from IR coupling.

Current topology (3 channels starred at PSU(−), HAT ribbon GND the only other bridge) is
**textbook-correct single-supply grounding**. Remaining lever = fatten the copper.

## Wall v2: two 300 W supplies for 384×128 (24 panels)

The v2 wall (ROADMAP §5) is 6×4 = **24** P4-2121-64x32 tiles, so one 60 A brick
no longer covers it: at the wire-gauge section's ~7.5 A/panel worst case that's
~180 A (~900 W) of all-white headroom. v2 runs **2× 5 V / 300 W / 60 A**
transformers (2nd arrives 2026-07-17) — 600 W / 120 A total, which comfortably
carries audio-reactive content (rarely near full white) but **not** a sustained
all-white 24-panel frame, so brightness capping / content limits (ROADMAP §1)
matter more at this size. Two supplies = two rails; extend the single-supply
star-ground rules above:
- **Bond both PSU (−) terminals at ONE star point** (a single heavy strap) so
  the two halves and the HAT/SBC logic GND share one reference. Don't let panel
  return current find a second path through the logic ground.
- **Split the wall by supply** (e.g. 12 panels each); keep each rail's fat +5 V
  and return copper to its own panels. Do **not** cross-feed +5 V between the two
  rails — parallel supplies fight on load-share.
- The SBC/HAT logic rail rides **one** supply's (−) reference (or a small
  dedicated supply), never floating between the two.

If the wall grows past this, or the runs get long, the HV-distribution idea
below (buck 12/24 V → 5 V at each cluster) is the next step.

## Measuring correctly (Kelvin / differential)

Reference the scope (−) at the *exact node of interest* — panel logic GND at the panel
input when checking logic, SBC GND at the board when checking the SBC rail. A
"convenient" chassis/Ethernet-shell ground that carries return current **lies**. Or go
differential: **Ch A on +5 V, Ch B on local GND, display A − B** — rejects ground motion
entirely. To quantify the bounce directly: **Ch A = HAT GND, Ch B = panel power-lug GND,
A − B** *is* the ground bounce; watch it shrink as the heavy return is beefed up.

## v2 idea: distribute 12/24 V, buck to 5 V at each panel

Distributing at higher voltage is sound EE (PoE / 48 V datacenter logic): for fixed
power, higher V → lower I → **I²R loss falls with current²**, and a 1 V drop is 20 % of
5 V but 4 % of 24 V. Point-of-load conversion shrinks the high-current loop to inches and
the long haul carries ~1/5 the current → ~1/5 the ground bounce. Catches:
- **Size bucks to panel *peak*** (~7.5 A/panel here → a 5 V/5 A buck browns out on white;
  use ~10 A modules, or one beefy buck per 4-panel cluster).
- **12 V vs 24 V:** 24 V = less current/thinner wire but a harsher step-down ratio (lower
  efficiency, more heat); 12 V is the gentler sweet spot for these distances.
- Switching noise needs output filtering; transient response needs local bulk caps; buck
  output GNDs still common through the (now-quieter) HV return.

Verdict: elegant and scalable — earns its keep when the wall **expands**. For the working
8-panel wall, **fat copper + the star/ratiometric rules above are the 80/20**.
