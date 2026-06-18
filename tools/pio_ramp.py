"""Send a raw byte ramp over the 4-lane PIO parallel link — nibble/lane-order check.

Run on the Pi with the `phase6-parallel` firmware flashed and an RTT session
attached (`cargo run --bin phase6-parallel`). This streams a known frame of
0,1,2,3,… bytes; the firmware logs the first 8 received bytes each second.

Read the firmware's `rx[0..8]` line:
  00 01 02 03 04 05 06 07   -> correct, the default is right.
  00 10 20 30 40 50 60 70   -> nibbles swapped; rerun with --pio-no-nibble-swap.
  bits within a nibble mirrored (e.g. 00 08 04 0c ...) -> reverse the lane wiring
                                                          (DATA0<->DATA3, DATA1<->DATA2).

Launch from a LOCAL cwd (e.g. /tmp), not the ~/rayglow mount — lgpio's FIFO can't
live on the network mount. Run by PATH (tools/ isn't part of the installed
package; rayglow is, so the import resolves anywhere). Match --bytes to the
firmware FRAME_BYTES (32 KB for a 4-panel single chain; fixed-size handshake).

    cd /tmp
    sudo ~/venv/bin/python ~/rayglow/tools/pio_ramp.py --pio-clkdiv 16
"""
import argparse
import time

from rayglow.render.pio_out import PioOut


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bytes", type=int, default=32768,
                    help="frame size; match the firmware FRAME_BYTES (default 32768)")
    ap.add_argument("--pio-clkdiv", type=float, default=16.0,
                    help="slow clock for bring-up; lower once the order is locked")
    ap.add_argument("--pio-no-nibble-swap", action="store_true",
                    help="disable the per-byte nibble swap")
    ap.add_argument("--ready-gpio", type=int, default=25)
    args = ap.parse_args()

    frame = bytes(i & 0xFF for i in range(args.bytes))
    out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.ready_gpio,
                 nibble_swap=not args.pio_no_nibble_swap)
    print(f"ramp: {len(frame)} bytes, clkdiv={args.pio_clkdiv:g}, "
          f"nibble_swap={not args.pio_no_nibble_swap}. Ctrl-C to stop.")
    # Heartbeat: if this count climbs, the Pi is sending (got READY, clocked a
    # frame). If it stays 0, send() is blocked waiting on READY — a control-line
    # problem, not a data-lane one.
    sent = 0
    t0 = last = time.monotonic()
    try:
        while True:
            out.send(frame)
            sent += 1
            now = time.monotonic()
            if now - last >= 1.0:
                print(f"sent {sent} frames ({sent / (now - t0):.0f}/s)")
                last = now
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()


if __name__ == "__main__":
    main()
