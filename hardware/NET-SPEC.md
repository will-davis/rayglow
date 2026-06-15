# RP2350-HUB75-HAT — Net Specification (v1)

> The locked electrical intent for the level-shifting HAT, derived from
> `PROJECT-PLAN.md` §6 (pin map) and §9 (HAT scope), as amended by the
> scale-out decision (see memory `scale-out-and-hat-scope`). This is the input
> to schematic generation — schematic + layout must match it. **Lock before
> editing the schematic.**

## 1. Scope (what the HAT does / does not do)

- **Does:** 3.3 V→5 V level-shift all HUB75 logic via `SN74AHCT245`; present **2**
  HUB75 connectors (chains A & B = one 256×64 tile); break out spare GPIO + power
  for the Phase 5 SPI link and multi-board sync; carry a 5 V rail for the '245s
  with a common ground reference.
- **Does NOT:** power the panels (their 5 V goes direct to panel lugs, never
  through the HAT or the HUB75 ribbon — §9.3); contain any MCU support circuitry
  (the dev board owns crystal/flash/USB/core power).

## 2. Components

| Ref | Part | Symbol / Footprint | Notes |
|-----|------|--------------------|-------|
| J1 | Pi 40-pin header | `Connector_Generic:Conn_02x20_Odd_Even` / `PinSocket_2x20_P2.54mm_Vertical` | Female socket, plugs onto the RP2350-PiZero |
| J2 | HUB75 chain A | `Connector_Generic:Conn_02x08_Odd_Even` / `Connector_IDC:IDC-Header_2x08_P2.54mm_Vertical` | Box header to panel-1 IN |
| J3 | HUB75 chain B | same as J2 | Box header to panel-1 IN |
| J4 | SPI/sync breakout | `Connector_Generic:Conn_01x08` / `PinHeader_1x08_P2.54mm_Vertical` | spare GPIO + power |
| J5 | 5 V rail input | `Connector_Generic:Conn_01x02` / screw terminal 5.08 mm | from the panel 5 V supply (GND commoned) |
| U1, U3 | `SN74AHCT245DWR` | `74xx:74HC245` / `Package_SO:SOIC-20W_7.5x12.8mm_P1.27mm` | data + CLK/LAT buffer (chain A=U1, B=U3) |
| U2, U4 | `SN74AHCT245DWR` | same | OE + address buffer (chain A=U2, B=U4) |
| C1–C4 | 100 nF | `Capacitor_SMD:C_0805_2012Metric` | one per '245, at VCC |
| C5 | 100 µF | bulk electrolytic / `CP_*` | 5 V rail bulk near J5 |
| R1, R2 | 22 Ω | `Resistor_SMD:R_0805_2012Metric` | series term on CLK (R1=chain A, R2=chain B) |

> Symbol note: KiCad's `74xx` lib has no AHCT symbol; the `74HC245` symbol is
> pin-identical, so use it with **Value = `SN74AHCT245DWR`**.

## 3. '245 buffer mapping (DIR=VCC → A→B; /OE=GND → always enabled)

Each '245: pin 1 = DIR (→ 5V), pin 19 = /OE (→ GND), pin 20 = VCC (5V), pin 10 =
GND. A-side (pins 2–9) = 3.3 V inputs from the RP2350; B-side (pins 18–11) = 5 V
outputs to the HUB75 connector.

### U1 — chain A data + clk/lat  → J2
| '245 ch | A in (from GP) | B out → J2 pin (signal) |
|---|---|---|
| 1 | GP0  (A.R1) | 1 (R1) |
| 2 | GP1  (A.G1) | 2 (G1) |
| 3 | GP2  (A.B1) | 3 (B1) |
| 4 | GP3  (A.R2) | 5 (R2) |
| 5 | GP4  (A.G2) | 6 (G2) |
| 6 | GP5  (A.B2) | 7 (B2) |
| 7 | GP16 (CLK)  | → **R1 (22 Ω)** → 13 (CLK) |
| 8 | GP17 (LAT)  | 14 (LAT/STB) |

### U2 — chain A oe + address  → J2
| '245 ch | A in | B out → J2 pin |
|---|---|---|
| 1 | GP18 (OE) | 15 (/OE) |
| 2 | GP12 (A)  | 9  (A) |
| 3 | GP13 (B)  | 10 (B) |
| 4 | GP14 (C)  | 11 (C) |
| 5 | GP15 (D)  | 12 (D) |
| 6,7,8 | **GND** (unused inputs tied low) | — (B6–B8 unconnected) |

### U3 — chain B data + clk/lat  → J3   (mirror of U1)
GP6→R1, GP7→G1, GP8→B1, GP9→R2, GP10→G2, GP11→B2, GP16(CLK)→**R2 (22 Ω)**→J3.13,
GP17(LAT)→J3.14.

### U4 — chain B oe + address  → J3   (mirror of U2)
GP18(OE)→J3.15, GP12→J3.9(A), GP13→J3.10(B), GP14→J3.11(C), GP15→J3.12(D); ch6–8
inputs tied to GND.

> Per-connector control fan-out (§9): GP12–18 each drive **two** '245 inputs (one
> in the A group, one in the B group) so each connector gets its own buffered
> CLK/LAT/OE/ADDR copy — light load on the RP2350, clean edges to each panel.

## 4. HUB75 connector pinout (J2, J3) — plain HUB75, 1/16 scan

| pin | net | pin | net |
|---|---|---|---|
| 1 | R1 | 2 | G1 |
| 3 | B1 | 4 | GND |
| 5 | R2 | 6 | G2 |
| 7 | B2 | 8 | GND |
| 9 | A  | 10 | B |
| 11 | C | 12 | D |
| 13 | CLK | 14 | LAT |
| 15 | /OE | 16 | GND |

## 5. Pi header (J1) — ACTUAL RP2350-PiZero positions (see PIZERO-HEADER-PINOUT.md)

> ⚠️ NOT standard Pi BCM. The board transposes GPIO4↔14, GPIO5↔15, GPIO9↔12,
> GPIO10↔11 vs a real Pi (PROJECT-PLAN §6's "standard BCM" assumption was wrong —
> it only checked pin 12 = GPIO18, which happens to coincide). Read off the
> schematic's `40Pin OUT` block.

- **GPIO → net** (the '245 A-side sources): GP0=pin27, GP1=28, GP2=3, GP3=5,
  **GP4=8**, **GP5=10**, GP6=31, GP7=26, GP8=24, **GP9=32**, **GP10=23**,
  **GP11=19**, **GP12=21**, GP13=33, **GP14=7**, **GP15=29**, GP16=36, GP17=11,
  GP18=12. *(bold = transposed vs standard Pi)*
- **Breakout GPIOs** (→ J4): GP19=pin35, GP20=38, GP21=40, GP22=15, GP26=37,
  GP27=13. *(these sit at standard positions)*
- **3V3**: pins 1, 17 → net 3V3 (breakout reference). **GND**: pins
  6,9,14,20,25,30,34,39 → net GND.
- **Pi 5V (pins 2,4): NOT CONNECTED** — the HAT does not draw from or backfeed
  the dev board's 5 V. The '245 rail is the panel-supply 5 V via J5.

## 6. SPI/sync breakout (J4, 1×8)

| pin | net | intended Phase-5 use |
|---|---|---|
| 1 | GP19 | SPI (e.g. SCK via PIO) |
| 2 | GP20 | SPI (MOSI ← Pi 5) |
| 3 | GP21 | SPI (CS) |
| 4 | GP22 | SPI (MISO / spare) |
| 5 | GP26 | frame-commit sync (shared across boards) |
| 6 | GP27 | spare |
| 7 | 3V3 | logic reference |
| 8 | GND | return |

> Exact SPI/sync pin roles are finalized in Phase 5 (PIO-SPI on any of these);
> the HAT just passes the spare GPIO + power through.

## 7. Power & grounding (§9.3)

- **5V rail**: J5 pin1 → U1–U4 VCC (pin 20) + each '245 DIR (pin 1) + C1–C4 +
  C5 bulk.
- **GND (single common net)**: J5 pin2 ↔ J1 GND pins ↔ U1–U4 GND (pin 10) +
  /OE (pin 19) + C1–C5 + HUB75 GND pins (4/8/16 on J2,J3) + J4 GND + U2/U4 unused
  inputs. This common ground ties panel-supply GND, dev-board GND, and the '245
  rail GND together (required for the level shift to reference correctly).
- **Panel 5 V** is **off-board** (panel lugs) — not represented here.

## 8. ERC notes

- `PWR_FLAG` on **5V** (driven by J5, a passive connector), **GND**, and **3V3**
  (J1 passive pin) so ERC doesn't flag undriven power inputs.
- Tie U2/U4 unused A-inputs to GND (no floating CMOS inputs).
- Net-name list (for parity check against this spec): `GND`, `+5V`, `+3V3`,
  `GP0..GP18` (data/ctrl), `GP19..GP22,GP26,GP27` (breakout),
  `J2_R1..`, HUB75 nets, and `CLK_A_T`/`CLK_B_T` (post-series-R CLK to J2/J3).

## 9. Deliberately deferred to v2 / layout

- **Series termination on data lines** (only CLK is terminated in v1): the 3.3 V
  unbuffered SI was already clean over 4 chained panels (§11.7 retired), so data
  termination is unnecessary insurance for v1. Easy to add a resistor field later
  if a buffered-edge ringing shows up.
- Board outline / mounting holes / connector orientation → decided at layout
  (human-in-the-loop, GUI).
