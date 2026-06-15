# RP2350-PiZero — 40-pin header (J5) actual pinout

> Read directly off the board schematic's `40Pin OUT` block (`RP2350-PiZero.pdf`),
> confirmed 2026-06-12. **The labels below are the *actual RP2350 GPIO* net at
> each physical pin** — i.e. what the firmware's `pins.gpioN` reaches, and what
> the board silkscreen shows. Compare against the silkscreen to verify.
>
> ⚠️ These are **NOT** standard Raspberry Pi BCM positions. The schematic's
> separate "BCM" column is only the Pi-compat reference number for each physical
> slot; Waveshare wired different RP2350 GPIOs there. PROJECT-PLAN §6's "standard
> Pi BCM numbering" assumption is WRONG for several pins (see swap list below).

Physical layout (pin 1 = the corner nearest the SD/USB end, per the board):

| Left signal | Pin | Pin | Right signal |
|---|:--:|:--:|---|
| **3V3** | 1 | 2 | **5V** (VBUS) |
| GPIO2 (SDA) | 3 | 4 | **5V** (VBUS) |
| GPIO3 (SCL) | 5 | 6 | **GND** |
| **GPIO14** | 7 | 8 | **GPIO4** (TX) |
| **GND** | 9 | 10 | **GPIO5** (RX) |
| GPIO17 | 11 | 12 | GPIO18 |
| GPIO27 | 13 | 14 | **GND** |
| GPIO22 | 15 | 16 | GPIO23 |
| **3V3** | 17 | 18 | GPIO24 |
| **GPIO11** (SPI MOSI) | 19 | 20 | **GND** |
| **GPIO12** (SPI MISO) | 21 | 22 | GPIO25 |
| **GPIO10** (SPI SCLK) | 23 | 24 | GPIO8 (CE0) |
| **GND** | 25 | 26 | GPIO7 (CE1) |
| GPIO0 (ID_SDA) | 27 | 28 | GPIO1 (ID_SCL) |
| **GPIO15** | 29 | 30 | **GND** |
| GPIO6 | 31 | 32 | **GPIO9** |
| GPIO13 | 33 | 34 | **GND** |
| GPIO19 | 35 | 36 | GPIO16 |
| GPIO26 | 37 | 38 | GPIO20 |
| **GND** | 39 | 40 | GPIO21 |

## Swaps vs. a standard Raspberry Pi (the gotcha)

Four GPIO **pairs** are transposed relative to a real Pi's BCM positions — every
one of them a pin the HAT uses:

| Pair | This board | Standard Pi |
|---|---|---|
| GPIO4 ↔ GPIO14 | GPIO4=pin8, GPIO14=pin7 | GPIO4=pin7, GPIO14=pin8 |
| GPIO5 ↔ GPIO15 | GPIO5=pin10, GPIO15=pin29 | GPIO5=pin29, GPIO15=pin10 |
| GPIO9 ↔ GPIO12 | GPIO9=pin32, GPIO12=pin21 | GPIO9=pin21, GPIO12=pin32 |
| GPIO10 ↔ GPIO11 | GPIO10=pin23, GPIO11=pin19 | GPIO10=pin19, GPIO11=pin23 |

GND and 3V3 positions, and GPIO16–27, are at standard positions. Pin 12 = GPIO18
happens to match standard (which is what mislead §6 into assuming the whole map
was standard — it verified the one pin that coincides).

## Consequences

- **Firmware: unaffected.** It addresses RP2350 GPIO *numbers* (`pins.gpio4`,
  etc.); the silicon GPIO4 is GPIO4 wherever it lands on the header. Your manual
  panel wiring worked because you used the silkscreen (= actual GPIO labels).
- **HAT PCB: affected.** The J1 (Pi-socket) → '245 routing must connect each
  physical pin to its *actual* GPIO net. The first netlist used standard-Pi
  positions and was wrong for the 8 swapped pins — corrected from this table.
