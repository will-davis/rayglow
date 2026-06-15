"""
Circuit-as-code generator for the RP2350-HUB75-HAT (see pcb/NET-SPEC.md).

Emits a KiCad netlist for PCB layout, after running SKiDL's ERC. The four
'245 blocks are built by one parametric `wire_chain()` call per chain — the
repetition the spec is full of becomes a loop, not four hand-placed copies.

Run:  uv run python gen_hat.py
Out:  rp2350-hub75-hat.net  (import into a KiCad PCB to lay out)
"""

import os

os.environ["KICAD8_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import (
    Part, Net, ERC, generate_netlist, lib_search_paths, KICAD8,
    set_default_tool, POWER,
)

set_default_tool(KICAD8)
for _t in list(lib_search_paths.keys()):
    lib_search_paths[_t].append("/usr/share/kicad/symbols")

# --- Footprints -------------------------------------------------------------
FP_245 = "Package_SO:SOIC-20W_7.5x12.8mm_P1.27mm"
FP_HUB = "Connector_IDC:IDC-Header_2x08_P2.54mm_Vertical"
FP_SOCK = "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical"
FP_HDR8 = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
FP_TERM = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
FP_C0805 = "Capacitor_SMD:C_0805_2012Metric"
FP_CP = "Capacitor_SMD:CP_Elec_6.3x7.7"
FP_R0805 = "Resistor_SMD:R_0805_2012Metric"

# --- Power + signal nets ----------------------------------------------------
gnd = Net("GND")
p5v = Net("+5V")
p3v3 = Net("+3V3")
for _n in (gnd, p5v, p3v3):
    _n.drive = POWER

# RP2350 GPIO nets we use: data/ctrl GP0..18, breakout GP19..22,26,27.
GP_NUMS = list(range(0, 19)) + [19, 20, 21, 22, 26, 27]
gp = {n: Net(f"GP{n}") for n in GP_NUMS}


def mk245(ref):
    """A '245 with DIR=A->B (5V), /OE=GND, powered. SN74AHCT245 on the HC symbol."""
    u = Part("74xx", "74HC245", ref=ref, value="SN74AHCT245DWR", footprint=FP_245)
    u[1] += p5v   # pin 1  = DIR (A->B): high => A->B
    u[19] += gnd  # pin 19 = /OE: low => enabled
    u[20] += p5v  # pin 20 = VCC
    u[10] += gnd  # pin 10 = GND
    return u


# --- Connectors -------------------------------------------------------------
J1 = Part("Connector_Generic", "Conn_02x20_Odd_Even", ref="J1",
          value="RP2350-PiZero", footprint=FP_SOCK)
J2 = Part("Connector_Generic", "Conn_02x08_Odd_Even", ref="J2",
          value="HUB75_A", footprint=FP_HUB)
J3 = Part("Connector_Generic", "Conn_02x08_Odd_Even", ref="J3",
          value="HUB75_B", footprint=FP_HUB)
J4 = Part("Connector_Generic", "Conn_01x08", ref="J4",
          value="SPI_SYNC", footprint=FP_HDR8)
J5 = Part("Connector_Generic", "Conn_01x02", ref="J5",
          value="+5V_IN", footprint=FP_TERM)

# --- '245 buffers -----------------------------------------------------------
U1 = mk245("U1")  # chain A data + CLK/LAT
U2 = mk245("U2")  # chain A OE + ADDR
U3 = mk245("U3")  # chain B data + CLK/LAT
U4 = mk245("U4")  # chain B OE + ADDR

# --- CLK series-termination resistors --------------------------------------
R1 = Part("Device", "R", ref="R1", value="22", footprint=FP_R0805)  # chain A CLK
R2 = Part("Device", "R", ref="R2", value="22", footprint=FP_R0805)  # chain B CLK


def wire_chain(data_u, ctrl_u, conn, rgb_gps, r_clk):
    """Wire one HUB75 chain: 6 RGB + buffered CLK/LAT/OE/ADDR -> connector."""
    # RGB: data '245 A0..A5 <- GP, B0..B5 -> connector RGB pins
    rgb_out = [1, 2, 3, 5, 6, 7]  # J: R1,G1,B1,R2,G2,B2
    for i in range(6):
        data_u[f"A{i}"] += gp[rgb_gps[i]]
        data_u[f"B{i}"] += conn[rgb_out[i]]
    # CLK (A6/B6) through series R; LAT (A7/B7) direct
    data_u["A6"] += gp[16]            # CLK in
    data_u["A7"] += gp[17]            # LAT in
    clk_pre = Net()                   # buffered CLK before the series resistor
    data_u["B6"] += clk_pre
    r_clk[1] += clk_pre
    r_clk[2] += conn[13]              # CLK -> connector (post-R)
    data_u["B7"] += conn[14]          # LAT -> connector

    # OE (A0/B0) + address A..D (A1..A4 / B1..B4)
    ctrl_u["A0"] += gp[18]            # OE in
    ctrl_u["B0"] += conn[15]          # /OE -> connector
    addr_gp = [12, 13, 14, 15]
    addr_pin = [9, 10, 11, 12]        # J: A,B,C,D
    for i in range(4):
        ctrl_u[f"A{i + 1}"] += gp[addr_gp[i]]
        ctrl_u[f"B{i + 1}"] += conn[addr_pin[i]]
    # Unused ctrl '245 inputs tied low (no floating CMOS inputs)
    for i in (5, 6, 7):
        ctrl_u[f"A{i}"] += gnd
    # Connector grounds
    for pin in (4, 8, 16):
        conn[pin] += gnd


wire_chain(U1, U2, J2, [0, 1, 2, 3, 4, 5], R1)        # chain A: GP0-5
wire_chain(U3, U4, J3, [6, 7, 8, 9, 10, 11], R2)      # chain B: GP6-11

# --- Pi header J1: GPIO sources + power/ground ------------------------------
# Physical pin -> ACTUAL RP2350 GPIO from the board schematic (see
# PIZERO-HEADER-PINOUT.md). NOT standard Pi BCM positions: GPIO4<->14,
# GPIO5<->15, GPIO9<->12, GPIO10<->11 are each transposed vs a real Pi.
j1_net = {
    1: p3v3, 3: gp[2], 5: gp[3], 7: gp[14], 8: gp[4], 10: gp[5],
    11: gp[17], 12: gp[18], 13: gp[27], 15: gp[22], 17: p3v3,
    19: gp[11], 21: gp[12], 23: gp[10], 24: gp[8], 26: gp[7],
    27: gp[0], 28: gp[1], 29: gp[15], 31: gp[6], 32: gp[9], 33: gp[13],
    35: gp[19], 36: gp[16], 37: gp[26], 38: gp[20], 40: gp[21],
}
for pin, net in j1_net.items():
    J1[pin] += net
for pin in (6, 9, 14, 20, 25, 30, 34, 39):  # header GND pins
    J1[pin] += gnd
# Pins 2,4 (Pi 5V) and 16,18,22 (unused GP23/24/25) intentionally left NC.

# --- SPI/sync breakout J4 ---------------------------------------------------
J4[1] += gp[19]   # SCK candidate
J4[2] += gp[20]   # MOSI (<- Pi 5)
J4[3] += gp[21]   # CS
J4[4] += gp[22]   # MISO / spare
J4[5] += gp[26]   # frame-commit sync
J4[6] += gp[27]   # spare
J4[7] += p3v3
J4[8] += gnd

# --- 5V rail input J5 -------------------------------------------------------
J5[1] += p5v
J5[2] += gnd

# --- Decoupling + bulk ------------------------------------------------------
for i in range(1, 5):  # one 100nF per '245
    c = Part("Device", "C", ref=f"C{i}", value="100nF", footprint=FP_C0805)
    c[1] += p5v
    c[2] += gnd
c5 = Part("Device", "C", ref="C5", value="100uF", footprint=FP_CP)
c5[1] += p5v
c5[2] += gnd

# Power rails are marked POWER-driven (gnd/p5v/p3v3 .drive = POWER above), which
# satisfies SKiDL ERC without PWR_FLAG parts. (A KiCad *schematic* would want
# PWR_FLAGs, but we drive layout straight from this netlist, so they'd only add
# a false TRISTATE<->POWER-OUT conflict against the grounded unused '245 inputs.)

ERC()
generate_netlist(file_="rp2350-hub75-hat.net")
print("netlist written: rp2350-hub75-hat.net")
