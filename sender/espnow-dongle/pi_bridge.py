#!/usr/bin/env python3
"""Serial -> UDP bridge for the RayGLow ESP-NOW mic link.

Runs on the Pi. Reads v1 feature packets (564 B, magic "MILK") arriving over UART
from the XIAO C3 dongle and forwards each verbatim to the renderer's UDP port.
This makes the ESP-NOW mic a drop-in UDP source: `rayglow/feed/receiver.py` is
unchanged and binds 0.0.0.0:5005, so the desktop `sender.py` can still override on
the same port (latest-wins by seq). Run one active source at a time.

Deploy: the Pi venv already has pyserial via `uv pip install -e '.[pi]'`.
  ~/venv/bin/python sender/espnow-dongle/pi_bridge.py --port /dev/ttyAMA0
See rayglow-mic-bridge.service for the systemd unit.
"""
import argparse
import socket
import struct
import sys
import time

MAGIC = 0x4D494C4B            # "MILK"
MAGIC_LE = struct.pack("<I", MAGIC)
PKT_LEN = 564                 # v1 packet size
V1_VERSION = 1                # uint16 at offset 4


def extract_frames(buf: bytearray):
    """Pull complete v1 frames out of `buf`, mutating it in place.

    Frames are fixed-length and back-to-back, but we resync on the 4-byte MAGIC so
    a mid-stream connect or a dropped byte self-heals. A MAGIC-looking sequence can
    appear inside the float payload by chance, so each candidate is validated by
    its version field (offset 4 == 1) before being accepted; a false hit is skipped
    by one byte and the search continues. Yields each 564-byte frame as `bytes`.
    """
    while True:
        i = buf.find(MAGIC_LE)
        if i < 0:
            # No magic yet; keep only a possible partial magic at the tail.
            if len(buf) > 3:
                del buf[:-3]
            return
        if i > 0:
            del buf[:i]                       # drop garbage before the magic
        if len(buf) < PKT_LEN:
            return                            # wait for the rest of the frame
        version = struct.unpack_from("<H", buf, 4)[0]
        if version != V1_VERSION:
            del buf[:4]                       # false magic in payload -> skip it
            continue
        frame = bytes(buf[:PKT_LEN])
        del buf[:PKT_LEN]
        yield frame


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="/dev/ttyAMA0", help="UART device from the dongle")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--host", default="127.0.0.1", help="renderer UDP host")
    ap.add_argument("--udp-port", type=int, default=5005)
    ap.add_argument("--debug", action="store_true", help="1 Hz forward-rate line")
    args = ap.parse_args()

    try:
        import serial  # pyserial (Pi extra); imported lazily so --help works anywhere
    except ImportError:
        sys.exit("pyserial not installed. On the Pi: uv pip install --python "
                 "~/venv/bin/python -e '.[pi]'")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = (args.host, args.udp_port)
    buf = bytearray()
    fwd = 0
    last = time.monotonic()

    while True:  # reconnect loop for service robustness
        try:
            ser = serial.Serial(args.port, args.baud, timeout=0.1)
        except serial.SerialException as e:
            print(f"open {args.port} failed: {e}; retrying in 2s", flush=True)
            time.sleep(2)
            continue
        print(f"bridge up: {args.port}@{args.baud} -> {dst[0]}:{dst[1]}", flush=True)
        try:
            while True:
                # Read whatever is buffered NOW (don't wait to fill a fixed block:
                # read(4096) would batch ~117 ms of packets and add that as latency).
                # in_waiting>0 -> return immediately; idle -> block briefly for 1 byte.
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    buf.extend(chunk)
                    for frame in extract_frames(buf):
                        sock.sendto(frame, dst)
                        fwd += 1
                if args.debug:
                    now = time.monotonic()
                    if now - last >= 1.0:
                        print(f"fwd {fwd}/s -> {dst[0]}:{dst[1]}", flush=True)
                        fwd = 0
                        last = now
        except serial.SerialException as e:
            print(f"serial error: {e}; reopening", flush=True)
            ser.close()
            time.sleep(1)


if __name__ == "__main__":
    main()
