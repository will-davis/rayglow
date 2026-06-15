# RayGLow hardware — RP2350-HUB75 HAT

The custom PCB that sits between the **Waveshare RP2350-PiZero** dev board and the
LED wall. It is deliberately minimal: it does **3.3 V → 5 V level-shifting** of all
HUB75 logic (via `SN74AHCT245` buffers), presents the **two HUB75 connectors**
(chains A & B = one 256×64 tile), breaks out the spare GPIO + power for the Pi 5 ↔
RP2350 SPI link, and carries a dedicated 5 V rail for the buffers. It does **not**
power the panels (their 5 V goes straight to the panel lugs) and contains no MCU
support circuitry — the dev board owns crystal/flash/USB/core power.

See the repo-root [`README.md`](../README.md) for where this sits in the pipeline,
and [`../firmware/`](../firmware/) for the firmware that drives the panels through
this HAT.

## Doc map

| File | What |
|---|---|
| [`NET-SPEC.md`](NET-SPEC.md) | **The locked electrical spec** — component list, '245 mappings, HUB75 pinout, power/grounding rules. The input to schematic generation. Lock before editing the schematic. |
| [`PIZERO-HEADER-PINOUT.md`](PIZERO-HEADER-PINOUT.md) | Physical header pin → **actual** RP2350 GPIO map, read off the board schematic. Critical: the Waveshare board is **not** standard Pi BCM (it transposes a few GPIO pairs). |
| [`NETLIST-REVIEW.md`](NETLIST-REVIEW.md) | QA notes on the generated netlist. |
| [`KICAD-AGENT-TOOLING.md`](KICAD-AGENT-TOOLING.md) | How the schematic was generated with SKiDL + which parts are human-in-the-loop (routing). |
| [`../docs/design-history/PROJECT-PLAN.md`](../docs/design-history/PROJECT-PLAN.md) | §6 (pin map) and §9 (HAT scope) — the original design intent. |

## Layout & fab

```
hardware/
├── NET-SPEC.md / PIZERO-HEADER-PINOUT.md / NETLIST-REVIEW.md   # design docs
├── gen_hat.py / gen_hat_sklib.py    # SKiDL schematic generators (uv project)
├── pyproject.toml / uv.lock         # SKiDL + KiCad-symbol deps for the above
├── rp2350-hub75-hat.net             # generated netlist
├── rp2350-rgb-pcb/                  # the KiCad project (.kicad_pcb/.pro/.sch)
└── fab/                             # plotted Gerbers + drill files (the fab deliverable)
```

The schematic is generated from `gen_hat.py` (run with `uv run gen_hat.py`); the
PCB layout (`rp2350-rgb-pcb/`) is hand-routed in KiCad. The `fab/` Gerbers are the
manufacturing output — send that folder (zipped) to a board house.

> Bench-gear note: the original plan's §14 instrumentation reality-check (e.g. the
> ≥100 MHz scope needed to validate 256-wide pixel-clock signal integrity) lives in
> [`../docs/design-history/PROJECT-PLAN.md`](../docs/design-history/PROJECT-PLAN.md).
