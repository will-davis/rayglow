# esp32-mic — microphone feature-sender

An ESP32 alternative to the desktop `../sender.py`. It listens through an INMP441
I2S MEMS microphone, runs a faithful C++ port of the MilkDrop band analysis, and
unicasts the **identical v1 UDP feature packet** (564 B) to the Pi renderer at
60 Hz. Because it's a standalone battery-/USB-powered box, the audio pickup can
live anywhere — decoupled from the Pi and the panel.

It shares **no code** with `sender.py` — only the packet contract
(`rayglow/feed/receiver.py`'s v1 layout), re-ported here.

> **Run one sender at a time per Pi.** This and `sender.py` share the same `seq`
> space; the receiver is latest-wins and they'd fight. The ESP *replaces* the
> desktop sender for a rig.

**Transports:** the send path is pluggable behind `src/transport.h`. The default
build (`s3zero`) uses WiFi/UDP (`net_udp.cpp`, keeps OTA). The production build
`s3zero-espnow` (`-D TRANSPORT_ESPNOW`, `net_espnow.cpp`) sends via **ESP-NOW** to
the `../espnow-dongle` receiver instead — lower jitter, no WiFi. Set `DONGLE_MAC`
+ `ESPNOW_CHANNEL` in `config.h`; flash over USB-C (no OTA in that build). See
`../espnow-dongle/README.md` for the full ESP-NOW link + Pi wiring.

## Hardware

**Board:** Waveshare ESP32-S3-Zero (ESP32-S3FH4R2, 4 MB flash / 2 MB PSRAM,
native USB-C). Also builds for an ESP32-WROOM-32 via the `esp32dev` env — the
I2S API is identical, only the pins differ. **Mic:** INMP441 I2S MEMS breakout.

INMP441 → S3-Zero wiring (pins set in `platformio.ini`, env `s3zero`):

| INMP441 | S3-Zero pad | notes |
|---------|-------------|-------|
| VDD     | 3V3         | not 5V |
| GND     | GND         | |
| L/R     | GND         | selects the LEFT I2S slot |
| WS      | IO6         | word select (LR clock) |
| SCK     | IO5         | bit clock |
| SD      | IO4         | serial data out → ESP `din` |

Pins IO4/5/6 are safe general GPIOs. Avoid **IO19/IO20** (native USB D−/D+),
**IO21** (onboard WS2812 RGB LED), and strapping pins **IO0/IO3/IO45/IO46**.

WROOM equivalents (env `esp32dev`): SD=IO32, SCK=IO14, WS=IO15.

## Toolchain

PlatformIO Core (CLI), installed via uv:

```fish
uv tool install platformio          # provides ~/.local/bin/pio
```

Serial access on Arch: install PlatformIO's udev rules once (sets the port to
mode 0666 and stops ModemManager grabbing it). Covers both the WROOM's CP210x
(`10c4:ea60` → `/dev/ttyUSB0`) and the S3-Zero's native USB-Serial/JTAG
(`303a:*` → `/dev/ttyACM0`):

```fish
sudo cp ~/.local/share/uv/tools/platformio/lib/python*/site-packages/platformio/assets/system/99-platformio-udev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty --action=add
```

## Configure

```fish
cp src/config.example.h src/config.h   # already present with placeholders
$EDITOR src/config.h                    # set WIFI_SSID/PASS, RAYGLOW_HOST (Pi IP)
```

`config.h` is gitignored (creds stay local, mirroring the repo's LOCAL-SETUP
convention).

## Build & flash

```fish
cd sender/esp32-mic
pio run                                 # build the default s3zero env
pio run -e s3zero -t upload -t monitor  # USB flash + open serial (115200)
```

**First flash over native USB:** the S3-Zero has no UART bridge — flashing and
the serial console both ride the S3's built-in USB (it enumerates as
`/dev/ttyACM0`). PlatformIO auto-resets into the bootloader, but if the first
upload can't connect, enter download mode by hand: **hold BOOT (IO0), tap RESET,
release BOOT**, then re-run upload. After that first flash, `ARDUINO_USB_CDC_ON_BOOT`
keeps `Serial` on the USB-C cable so `-t monitor` works normally.

You should see a 1 Hz status line once WiFi is up:

```
bass 1.03 mid 0.98 treb 1.12 sub 0.87 vol 1.01 | raw b/m/t 0.0142/0.0091/0.0033 sub 0.0007
```

### OTA (after the first USB flash)

```fish
set -x RAYGLOW_MIC_IP <device-ip>
set -x RAYGLOW_MIC_OTA_PASS rayglow-ota   # must match OTA_PASSWORD in config.h
pio run -e s3zero-ota -t upload
```

### The ESP32-WROOM-32 (interim dev board)

```fish
pio run -e esp32dev -t upload   # same source, different board + pins
```

## Verify the wire contract

Point `RAYGLOW_HOST` at your desktop and run this to confirm the receiver would
accept the packets (correct length, magic, version, advancing seq):

```fish
python - <<'PY'
import socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", 5005))
for _ in range(5):
    d, a = s.recvfrom(2048)
    magic, ver, flags, seq = struct.unpack_from("<IHHI", d, 0)
    print(f"from {a[0]} len={len(d)} magic={magic:#010x} v{ver} flags={flags} seq={seq}")
    assert len(d) == 564 and magic == 0x4D494C4B and ver == 1
print("OK: valid v1 packets")
PY
```

Then, on the panel (desktop sender stopped):

```fish
sudo ~/venv/bin/python -m rayglow.render rayglow/render/presets/milk-verbose.glsl
```

The reference card should react to whatever the mic hears. Band placement check:
play 110 Hz / 6 kHz / 10 kHz tones near the mic → bass / mid / treb light up.

## Design notes

- **Calibration parity:** `SAMPLE_RATE`, `WINDOW`, `NFREQ`, the `EQUALIZE` table,
  `BAND_EDGES`, the 2048-pt sub window, and `AutoGain` all mirror `sender.py`
  exactly, so the Pi's texel calibration needs no change. The mic's acoustic
  response differs from line level, but AutoGain self-normalizes ("1.0 =
  typical"), so absolute scale doesn't matter.
- **Pacing:** the loop is paced by the I2S clock — a blocking read of `CHUNK`
  (800) samples takes exactly 1/60 s, so the audio hardware *is* the 60 Hz
  timebase. No separate wall-clock timer.
- **FFT seam:** `fft.{h,cpp}` has two backends behind one `fft_mag()`. The S3
  env (`-D USE_ESP_DSP`) links the precompiled `libespressif__esp-dsp.a` that
  ships inside `framework-arduinoespressif32-libs` (no lib_deps needed - just the
  include dirs + `-l` in `platformio.ini`) and calls `dsps_fft2r_fc32`, which
  dispatches to the S3's SIMD (`aes3`) path. The WROOM env keeps the portable
  scalar radix-2. Measured: DSP 5.2 ms scalar -> 2.5 ms SIMD.
- **Latency tuning:** `CHUNK` (in `dsp.h`) trades latency vs real-time margin -
  the loop period is `max(compute, CHUNK/48000)`, so `CHUNK` must exceed
  `compute*48000` or the DMA backlogs. With SIMD compute ~3 ms, `CHUNK=256`
  (5.33 ms, 187 Hz) is the current pick. The 1 Hz serial line reports per-stage
  `wait/dsp/send` ms so you can retune empirically. Remaining latency is now
  dominated by WiFi jitter (measure with `ping` from the Pi) and the inherent
  ~21 ms group delay of the 2048-pt sub window (shrink `SUB_WINDOW` to trade
  sub-bass resolution for snap).
- **Scope:** emits v1 (bands + true sub). v2 (spectrum/chroma/beat) is a future
  add via the same FFT seam; stereo stays zero (mono mic).
