# espnow-dongle — ESP-NOW → UART receiver for the Pi

A **Seeed XIAO ESP32-C3** that lives inside the Pi case, receives v1 feature
packets over **ESP-NOW** from the `../esp32-mic` sender, and relays them verbatim
to the Pi over **UART**. A small Pi-side bridge (`pi_bridge.py`) forwards them to
the renderer's UDP port — so `rayglow/feed/receiver.py` is **unchanged** and the
desktop `sender.py` can still override on the same port (latest-wins).

Why: ESP-NOW is connectionless (no AP, no association, no IP stack), which removes
the WiFi/UDP failure modes — AP-buffering jitter and lwIP TX-pool `ENOMEM` — that
the congested 2.4 GHz band was causing. Verified: `tx == rx == fwd`, `fail 0`.

```
S3 mic (pure ESP-NOW, ch 1) ──unicast v1 564B──▶ XIAO C3 dongle
    ──UART (D6/GPIO21 TX → Pi RX, 921600 8N1)──▶ pi_bridge.py ──UDP──▶ 127.0.0.1:5005 ──▶ receiver.py
```

## Wiring — XIAO C3 → Raspberry Pi 5

Only three wires (data is one-way, dongle → Pi). All 3.3 V logic — **no level
shifter**. The **U.FL antenna must be attached** (mount it outside the case).

| XIAO C3 pad | → | Pi 5 header | Notes |
|-------------|---|-------------|-------|
| **5V**  | → | pin **2** (5V)        | powers the C3 via its onboard LDO |
| **GND** | → | pin **6** (GND)       | common ground |
| **D6 / GPIO21 (TX)** | → | Pi **GPIO9 / pin 21** (uart3 RXD3) | data: dongle → Pi |

**Off-limits Pi pins** (used by the default PIO panel transport — `render/pio_out.py`):
BCM **12,13,14,15** (DATA0-3), **20** (DCLK), **21** (CS), **25** (READY). That's
why we can't use the primary UART on pins 8/10 (BCM14/15). GPIO8/9 (SPI0, unused
in PIO mode) are free, and that's where `uart3` lives on the Pi 5.

## Enable the spare Pi UART

**Pi-5 gotcha:** `dtoverlay=uart3` is documented as GPIOs 4-7 but that's *BCM2711
(Pi 4) only*. On the **Pi 5**, uart3 maps to **GPIO8 (TXD3) / GPIO9 (RXD3)** — so
the dongle's TX wires to **GPIO9 = physical pin 21**. Use the Pi-5 overlay name:

```
dtoverlay=uart3-pi5      # GPIO8/9; plain `uart3` also resolves here on Pi 5
```

Reboot, then confirm the node + pin mapping (this is the authoritative check —
don't trust the overlay's advertised pins):

```fish
ls -l /dev/serial* /dev/ttyAMA*        # the new UART, e.g. /dev/ttyAMA3
pinctrl get | rg 'RX|TX'               # expect: 9: ... GPIO9 = RXD3
```

Wire the dongle TX to whichever GPIO shows as `RXDn`, at its physical pin.

## ESP-NOW channel + dongle MAC

Both ends are pinned to **channel 1** (`ESPNOW_CHANNEL` in `src/main.cpp` and the
mic's `config.h` — they must match). The dongle prints its MAC on boot; that value
goes into the mic's `config.h` as `DONGLE_MAC`. This dongle's MAC:
`98:3D:AE:AC:A6:60` → `#define DONGLE_MAC {0x98,0x3D,0xAE,0xAC,0xA6,0x60}`.

## Build & flash the dongle (over USB, before sealing the case)

```fish
cd sender/espnow-dongle
pio run -e xiao_c3 -t upload -t monitor    # native USB -> /dev/ttyACM*
```

Boot print shows the MAC + a 1 Hz `rx / fwd / bad` counter. Flash it **before**
mounting — its relay firmware is stable, so no OTA is needed inside the case.

## Run the Pi bridge

The Pi venv gets `pyserial` from the `.[pi]` extra:

```fish
uv pip install --python ~/venv/bin/python -e '.[pi]'
```

Run it (point `--port` at the UART from above):

```fish
~/venv/bin/python sender/espnow-dongle/pi_bridge.py --port /dev/ttyAMA3 --baud 921600 --debug
```

Or install the service (`rayglow-mic-bridge.service` — edit paths/port/user first):

```fish
sudo cp rayglow-mic-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rayglow-mic-bridge
journalctl -u rayglow-mic-bridge -f
```

## Verify end-to-end

1. **Dongle** (`pio ... -t monitor`): `rx N  fwd N  bad 0` climbing when the mic runs.
2. **Mic** (`esp32-mic`, flashed with `-e s3zero-espnow`): `ESP-NOW ... tx N fail 0`.
3. **Bridge** (`--debug`): `fwd N/s`. Confirm the renderer's socket sees it:
   ```fish
   python3 -c "import socket,struct; s=socket.socket(2,2); s.bind(('0.0.0.0',5005))
   [print(a[0],len(d),'seq',struct.unpack_from('<I',d,8)[0]) for d,a in (s.recvfrom(2048) for _ in range(5))]"
   ```
   → `127.0.0.1 564 seq …` climbing.
4. **Panel:** bridge running, `sender.py` stopped → the wall reacts to the mic.

## Override with the pure desktop feed

The bridge is a drop-in UDP source. To hand control to the ambient-free pipewire
feed: `sudo systemctl stop rayglow-mic-bridge` and run `sender.py` on the desktop.
Restart the bridge to return to the mic. (Shared `seq` space = one active source
at a time.)
