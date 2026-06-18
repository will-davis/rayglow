"""Shadertoy renderer entry point.

Headless dry-run (no root, no hardware; writes an animated GIF):
    ~/venv/bin/python -m rayglow.render ../example.glsl --dry-run 120

Hardware (user runs this; root needed for GPIO):
    sudo ~/venv/bin/python -m rayglow.render ../example.glsl

Texture/audio channels — bind iChannel0..3 with --channelN flags or, better,
`// iChannelN: spec` comment directives inside the shader file:
    sudo ~/venv/bin/python -m rayglow.render presets/foo.glsl \\
        --channel0 audio --channel1 noise --channel2 pic.png

'audio' is the Shadertoy 512x2 spectrum/waveform texture, fed live from the
milk UDP feed (port 5005) with the usual synth fallback when no packets
arrive; --no-listen skips the socket entirely (synth only).  Dry-run never
listens.  'milk' is an 8x1 float texture of the packet's auto-gained band
scalars (bass/mid/treb/vol/sub, 1.0 = typical, hits spike 2-3) plus derived
signals per band (d/dt, ~125ms envelope, integrated phase) and packet
liveness — see MilkChannel in textures.py for the texel map.  Use it when
the audio texture's clamped spectrum feels binary.

Multipass (Shadertoy Buffer A-D): sibling files next to foo.glsl named
foo.bufA.glsl .. foo.bufD.glsl are auto-discovered and rendered in Shadertoy
order (A,B,C,D, then image) into float ping-pong buffers.  Wire inputs with
directives in each pass file, e.g. in foo.bufA.glsl:
    // iChannel0: self          <- bufA's own previous frame
and in foo.glsl:
    // iChannel0: bufA

While running on hardware, edit any of the .glsl files in another window and
save — the panel recompiles live (compile errors print here; last good
shader keeps showing, and buffer state survives the reload).
"""
import argparse
import os
import re
import sys
import threading
import time

import numpy as np

from ..feed import config  # geometry/gamma source of truth (shared feed pkg)

from .egl import GLContext, GLError
from .pipeline import ShaderToy
from .reload import GlslWatcher


def pin_to_core(core):
    """Pin the render thread to a dedicated core so frame pacing doesn't fight
    scheduler migration.  Per-thread on Linux (affects only this thread)."""
    try:
        os.sched_setaffinity(0, {core})
    except OSError as e:
        print(f"warning: could not pin to core {core}: {e}", file=sys.stderr)


class AudioFeed:
    """Owns the milk feature state (+ lazily bound UDP receiver) and pushes
    the waveform into every audio channel each frame.  `channels` is the
    ShaderToy's live audio_channels list — a hot reload that introduces an
    audio directive starts feeding (and listening) without a restart.
    Synth fallback keeps the texture animating when nothing is playing."""

    def __init__(self, channels, allow_listen):
        from ..feed.features import FeatureState
        self.features = FeatureState()
        self.channels = channels
        self.allow_listen = allow_listen
        self.receiver = None
        self._announced = False

    def update(self, t, dt):
        if not self.channels:
            return
        if not self._announced:
            self._announced = True
            if self.allow_listen:
                from ..feed.receiver import Receiver
                self.receiver = Receiver()
                print("audio: listening on UDP")
            else:
                print("audio: synth fallback only")
        pkt = self.receiver.poll() if self.receiver else None
        self.features.update(pkt, t, dt)
        for ch in self.channels:
            ch.update(self.features)


def compile_or_die(toy, name, src):
    ok, msg = toy.set_source(name, src)
    if not ok:
        print(f"GLSL compile error ({name}):\n{msg}", file=sys.stderr)
        sys.exit(1)
    if msg:
        print(f"warning ({name}): {msg}", file=sys.stderr)


def maybe_reload(toy, watchers):
    for name, watcher in watchers.items():
        if not watcher.changed():
            continue
        try:
            src = watcher.read()
        except OSError as e:
            print(f"reload: cannot read {watcher.path}: {e}", file=sys.stderr)
            continue
        ok, msg = toy.set_source(name, src)
        if ok:
            print(f"reloaded {watcher.path}"
                  + (f"  (warning: {msg})" if msg else ""))
        else:
            print(f"reload failed ({name}) — keeping last good shader:\n{msg}",
                  file=sys.stderr)


def run_dry(toy, feed, args):
    """Render N frames headlessly with a fixed synthetic clock, sanity-check
    the numerics, save a GIF."""
    from .output import save_gif
    frames = []
    dt = 1.0 / args.fps
    t0 = time.perf_counter()
    for i in range(args.dry_run):
        if feed:
            feed.update(i * dt, dt)
        frames.append(toy.render(i * dt, dt, i))
    elapsed = time.perf_counter() - t0
    fps = args.dry_run / elapsed

    stack = np.stack(frames)
    fmin, fmean, fmax = int(stack.min()), float(stack.mean()), int(stack.max())
    print(f"{args.dry_run} frames at {toy.width}x{toy.height} "
          f"(scale {toy.scale}) in {elapsed:.2f}s = {fps:.0f} fps")
    print(f"pixels: min={fmin} mean={fmean:.1f} max={fmax}")
    ok = True
    if fmax == 0:
        print("FAIL: output is all black")
        ok = False
    if fmean > 250:
        print("WARN: output near-saturated (mean > 250)")
    save_gif(frames, args.out, args.fps)
    print(f"wrote {args.out}")
    sys.exit(0 if ok else 1)


class _SendPipe:
    """Background SPI sender that overlaps frame N's transfer with frame N+1's
    render. `out.send()` blocks for the SPI floor + READY wait (~8 ms); running
    it on a worker thread lets the main thread render+pack the next frame
    meanwhile, so the loop cadence becomes max(render+pack, send) instead of
    their sum. Depth-1 (one frame in flight) keeps the added latency to a single
    frame. Only the worker touches `out`, so the SPI/GPIO objects stay
    single-threaded. The GIL is released during the spidev write and READY wait,
    so the overlap is real.
    """

    def __init__(self, out):
        self._out = out
        self._payload = None
        self._work = threading.Event()    # main -> worker: a payload is ready
        self._idle = threading.Event()    # worker -> main: previous send done
        self._idle.set()                  # start idle
        self._stop = False
        self._exc = None
        self.acc_send = 0.0               # worker: cumulative transfer seconds
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            self._work.wait()
            self._work.clear()
            if self._stop:
                return
            try:
                t = time.perf_counter()
                self._out.send(self._payload)
                self.acc_send += time.perf_counter() - t
            except BaseException as e:    # surface to main on next submit()
                self._exc = e
            self._idle.set()

    def submit(self, payload):
        """Block until the previous send finishes (the residual send-bound
        stall), hand off `payload`, and return that wait time in seconds. The
        worker transfers it while the caller renders the next frame."""
        t = time.perf_counter()
        self._idle.wait()
        wait = time.perf_counter() - t
        if self._exc is not None:
            raise self._exc
        self._idle.clear()
        self._payload = payload
        self._work.set()
        return wait

    def close(self):
        self._idle.wait()
        self._stop = True
        self._work.set()
        self._thread.join(timeout=2.0)
        self._out.close()


def run_spi(toy, watchers, feed, args):
    """Render + pack + ship frames to the rp2350b over SPI (the only output).

    The render readback is LINEAR (args.gamma forced to 1.0) and gets packed
    into bit-planes (hub75.pack, byte-identical to the firmware) before going
    out over SPI; the rp2350b applies the CIE gamma LUT downstream. The READY
    handshake self-paces: out.send() blocks until the rp2350b has armed its RX
    DMA, then pushes one 64 KB transfer.
    """
    from .hub75 import pack, pack_single, to_single_chain

    # Warm the full render+pack path before opening hardware (mirrors run_matrix).
    if feed:
        feed.update(0.0, 1.0 / 60)
    warm = toy.render(0.0, 1.0 / 60, 0)
    if config.SPI_SINGLE_CHAIN:
        pack_single(to_single_chain(warm))
    else:
        pack(warm)

    # Transport: the 1-lane SPI link (default) or the 8-lane RP1-PIO parallel bus.
    # Both expose send(bytes)/close(); the byte stream is identical either way.
    if args.transport == "pio":
        from .pio_out import PioOut
        out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.ready_gpio,
                     nibble_swap=not args.pio_no_nibble_swap)
    else:
        from .spi_out import SpiOut
        out = SpiOut(args.spi_hz, ready_bcm=args.ready_gpio)
    # Build the send worker BEFORE pinning, so it inherits the full-core affinity
    # and floats onto an idle core; pin_to_core then pins only the render thread.
    pipe = _SendPipe(out)
    pin_to_core(config.RENDER_CORE)

    frame_interval = 1.0 / args.fps
    t0 = time.perf_counter()
    last = t0
    fps_frames, fps_t = 0, t0
    frame = 0
    # Per-stage accumulators. render+pack run on this thread; the SPI transfer
    # runs on the worker (pipe.acc_send). `acc_wait` is how long this thread
    # blocks waiting for the previous transfer — the residual send-bound stall
    # AFTER overlap (≈0 => the link is fully hidden behind render).
    acc_render = acc_pack = acc_wait = 0.0
    pipe.acc_send = 0.0
    last_bytes = 0
    try:
        while True:
            now = time.perf_counter()
            if args.duration and now - t0 >= args.duration:
                break
            maybe_reload(toy, watchers)
            if feed:
                feed.update(now - t0, now - last)
            ta = time.perf_counter()
            buf = toy.render(now - t0, now - last, frame)  # (H,W,3) uint8 LINEAR
            # Physical-install orientation (see config): the wall is rotated 180deg
            # from the rendered frame, so flip both axes before packing.
            if config.SPI_FLIP_V:
                buf = buf[::-1]
            if config.SPI_FLIP_H:
                buf = buf[:, ::-1]
            buf = np.ascontiguousarray(buf)
            # Single-chain rig: fold the logical wall into the 512-wide serpentine
            # strip (chain A) before packing. pack() infers the wider frame.
            if config.SPI_SINGLE_CHAIN:
                buf = to_single_chain(buf)
            tb = time.perf_counter()
            payload = pack_single(buf) if config.SPI_SINGLE_CHAIN else pack(buf)
            tc = time.perf_counter()
            # Hand the frame to the worker; it transfers while we render the next.
            # submit() blocks only if the previous transfer hasn't finished.
            wait = pipe.submit(payload)   # fresh immutable bytes => no aliasing
            acc_render += tb - ta         # GLSL render + readback + flips + fold
            acc_pack += tc - tb           # bit-plane packing
            acc_wait += wait              # stall on the previous send (overlap residue)
            last_bytes = len(payload)
            last = now
            frame += 1

            fps_frames += 1
            if now - fps_t >= 5.0:
                n = fps_frames
                # send = the worker's actual transfer time (link cost); wait =
                # how much it leaked into the critical path. If wait hugs 0 the
                # link is fully hidden and `render` is the clamp; if wait ~ send,
                # the link still paces. SPI floor is the theoretical transfer min.
                if args.transport == "pio":
                    # 8 lanes, 1 byte/clock, 2 SM cycles/byte off RP1's 200 MHz.
                    floor_ms = last_bytes / (200e6 / (2 * args.pio_clkdiv)) * 1e3
                    link = f"PIO floor {floor_ms:4.1f}ms @ clkdiv {args.pio_clkdiv:g}"
                else:
                    floor_ms = last_bytes * 8 / args.spi_hz * 1e3
                    link = f"SPI floor {floor_ms:4.1f}ms @ {args.spi_hz/1e6:.0f}MHz"
                send_ms = pipe.acc_send / n * 1e3
                print(f"{n / (now - fps_t):6.1f} fps | "
                      f"render {acc_render / n * 1e3:5.1f}ms  "
                      f"pack {acc_pack / n * 1e3:4.1f}ms  "
                      f"send {send_ms:5.1f}ms  wait {acc_wait / n * 1e3:5.1f}ms "
                      f"({link}, {last_bytes//1024}KB)")
                fps_frames, fps_t = 0, now
                acc_render = acc_pack = acc_wait = 0.0
                pipe.acc_send = 0.0
            # Cap to --fps so we don't render frames nobody asked for (the worker
            # + READY handshake otherwise self-pace to the rp2350b).
            sleep = frame_interval - (time.perf_counter() - now)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        pass
    finally:
        pipe.close()


def main():
    ap = argparse.ArgumentParser(
        prog="shadertoy", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shader", help="path to a .glsl file with Shadertoy "
                    "mainImage() code, pasted unchanged")
    ap.add_argument("--fps", type=float, default=60.0,
                    help="target fps cap (default 60)")
    ap.add_argument("--scale", type=int, default=4,
                    help="supersample factor (default 4; 1 = pixel-exact)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="readback gamma (default 1.0 = LINEAR; the rp2350b "
                         "firmware applies the CIE LUT, so correcting here too "
                         "would double-correct)")
    ap.add_argument("--transport", choices=("spi", "pio"), default="spi",
                    help="link to the rp2350b: 'spi' (1-lane, proven default) or "
                         "'pio' (8-lane RP1-PIO parallel bus — needs phase6 "
                         "firmware + piobridge/libpioshim.so)")
    ap.add_argument("--spi-hz", type=int, default=24_000_000,
                    help="SPI clock in Hz (--transport spi; start low, then ramp)")
    ap.add_argument("--pio-clkdiv", type=float, default=4.0,
                    help="RP1-PIO clock divisor (--transport pio); per-lane rate "
                         "≈ 200MHz/(2*div). Start high (slow), then lower")
    ap.add_argument("--pio-no-nibble-swap", action="store_true",
                    help="(--transport pio) disable the per-byte nibble swap — "
                         "use only if the logic analyzer shows nibbles arriving "
                         "un-swapped")
    ap.add_argument("--ready-gpio", type=int, default=25,
                    help="BCM pin reading the rp2350b READY line")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (default: run forever)")
    ap.add_argument("--dry-run", nargs="?", const=120, type=int, default=None,
                    metavar="N", help="headless: render N frames (default "
                    "120), save a GIF, no hardware")
    ap.add_argument("--out", default="/tmp/shadertoy_out.gif",
                    help="dry-run GIF path (default /tmp/shadertoy_out.gif)")
    ap.add_argument("--width", type=int, default=None,
                    help="render width (default: %d)" % config.SPI_WIDTH)
    ap.add_argument("--height", type=int, default=None,
                    help="render height (default: %d)" % config.SPI_HEIGHT)
    for i in range(4):
        ap.add_argument(f"--channel{i}", metavar="SPEC", default=None,
                        help=("iChannel0 source: 'audio', 'milk', "
                              "'noise[:seed[:size]]', or an image path "
                              "(likewise --channel1..3)"
                              if i == 0 else argparse.SUPPRESS))
    ap.add_argument("--no-listen", action="store_true",
                    help="audio channel: never bind the UDP socket, "
                         "synth fallback only")
    ap.add_argument("--pbo", action="store_true",
                    help="async PBO readback (experimental; measured SLOWER on "
                         "the Pi's V3D — default is the synchronous path)")
    args = ap.parse_args()

    # Geometry defaults to the full two-chain display (256x64). The render
    # readback is LINEAR (gamma 1.0) because the rp2350b owns the CIE gamma LUT
    # (config.SPI_GAMMA) — applying gamma here too would double-correct.
    if args.width is None:
        args.width = config.SPI_WIDTH
    if args.height is None:
        args.height = config.SPI_HEIGHT

    try:
        ctx = GLContext()
    except GLError as e:
        print(f"GL init failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"GPU: {ctx.info()}")

    # PBO async readback is experimental and off by default (slower on V3D, see
    # output.Readback); only ever for the live loop, never dry-run (the one-frame
    # shift/drop would skew the GIF).
    use_pbo = (args.dry_run is None) and args.pbo
    toy = ShaderToy(args.width, args.height, scale=args.scale,
                    gamma=args.gamma, use_pbo=use_pbo,
                    base_dir=os.path.dirname(os.path.abspath(args.shader)))
    for i in range(4):
        spec = getattr(args, f"channel{i}")
        if spec:
            toy.set_cli_channel(i, spec)

    # Multipass: foo.glsl + sibling foo.bufA.glsl .. foo.bufD.glsl.
    # All passes are created before any compile so buffer cross-references
    # resolve regardless of order; buffers compile first, image last.
    watchers = {}
    base = re.sub(r"\.glsl$", "", args.shader)
    for x in "ABCD":
        path = f"{base}.buf{x}.glsl"
        if os.path.exists(path):
            toy.add_buffer(f"buf{x}")
            watchers[f"buf{x}"] = GlslWatcher(path)
    watchers["image"] = GlslWatcher(args.shader)
    if len(watchers) > 1:
        print(f"multipass: {', '.join(watchers)} "
              f"(buffers: {toy.buffer_format[3]})")
    for name, watcher in watchers.items():
        compile_or_die(toy, name, watcher.read())

    dry = args.dry_run is not None
    feed = AudioFeed(toy.audio_channels,
                     allow_listen=not args.no_listen and not dry)

    if dry:
        run_dry(toy, feed, args)
    else:
        run_spi(toy, watchers, feed, args)


if __name__ == "__main__":
    main()
