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
listens.  'milk' is a 13x1 float texture of the packet's auto-gained band
scalars (bass/mid/treb/vol/sub, 1.0 = typical, hits spike 2-3) plus derived
signals per band (d/dt, ~125ms envelope, integrated phase), packet liveness,
and the v2 feed's scalar features (spectral descriptors, beat/tempo, stereo,
chroma) — see MilkChannel in textures.py for the texel map.  'spectrum' is a
512x1 float texture of the v2 feed's real log-spaced spectrum.  Use milk when
the audio texture's clamped spectrum feels binary, spectrum for a real shape.

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

--loop SECONDS cycles every standalone .glsl in the launched shader's folder,
switching on that interval and wrapping at the end (buffer siblings
foo.bufA.glsl..bufD.glsl are skipped; a shader that fails to compile is
skipped too). Each switch rebuilds the shader fresh, so iTime/iFrame restart
at 0 and multipass buffers start cleared:
    sudo ~/venv/bin/python -m rayglow.render presets/first.glsl --loop 30
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


def build_shader(args, use_pbo, shader_path, fatal=True):
    """Build a fresh ShaderToy + GlslWatchers for one shader file and its
    sibling buffer passes (foo.bufA.glsl .. foo.bufD.glsl).

    All passes are created before any compile so buffer cross-references
    resolve regardless of order; buffers compile first, image last.  CLI
    --channelN overrides apply to the image pass.

    fatal=True (initial launch): a compile error exits the process.
    fatal=False (--loop switch): a compile error prints, tears the half-built
    toy back down, and returns (None, None) so the caller skips that shader.
    """
    toy = ShaderToy(args.width, args.height, scale=args.scale,
                    gamma=args.gamma, use_pbo=use_pbo,
                    base_dir=os.path.dirname(os.path.abspath(shader_path)))
    for i in range(4):
        spec = getattr(args, f"channel{i}")
        if spec:
            toy.set_cli_channel(i, spec)

    watchers = {}
    base = re.sub(r"\.glsl$", "", shader_path)
    for x in "ABCD":
        path = f"{base}.buf{x}.glsl"
        if os.path.exists(path):
            toy.add_buffer(f"buf{x}")
            watchers[f"buf{x}"] = GlslWatcher(path)
    watchers["image"] = GlslWatcher(shader_path)
    if len(watchers) > 1:
        print(f"multipass: {', '.join(watchers)} "
              f"(buffers: {toy.buffer_format[3]})")
    for name, watcher in watchers.items():
        if fatal:
            compile_or_die(toy, name, watcher.read())
        else:
            ok, msg = toy.set_source(name, watcher.read())
            if not ok:
                print(f"--loop: skipping {shader_path} — compile error "
                      f"({name}):\n{msg}", file=sys.stderr)
                toy.destroy()
                return None, None
            if msg:
                print(f"warning ({name}): {msg}", file=sys.stderr)
    return toy, watchers


def build_playlist(shader_path):
    """Sorted absolute paths of the standalone .glsl files in shader_path's
    directory.  Buffer siblings (foo.bufA.glsl .. foo.bufD.glsl) are excluded
    — they're multipass inputs, not shaders to display on their own."""
    d = os.path.dirname(os.path.abspath(shader_path))
    buf_re = re.compile(r"\.buf[A-D]\.glsl$")
    names = sorted(f for f in os.listdir(d)
                   if f.endswith(".glsl") and not buf_re.search(f))
    return [os.path.join(d, n) for n in names]


def _next_compilable(playlist, index, args, use_pbo):
    """Walk forward from `index` (wrapping) and build the next shader that
    compiles.  Returns (new_index, toy, watchers), or None if a full lap finds
    nothing that compiles (caller keeps showing the current shader)."""
    n = len(playlist)
    for step in range(1, n):
        j = (index + step) % n
        toy, watchers = build_shader(args, use_pbo, playlist[j], fatal=False)
        if toy is not None:
            return j, toy, watchers
    return None


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
    """Background link sender that overlaps frame N's transfer with frame N+1's
    render. `out.send()` blocks for the transfer floor + READY wait; running
    it on a worker thread lets the main thread render+pack the next frame
    meanwhile, so the loop cadence becomes max(render+pack, send) instead of
    their sum. Depth-1 (one frame in flight) keeps the added latency to a single
    frame. Only the worker touches `out`, so the link/GPIO objects stay
    single-threaded. The GIL is released during the transfer (pioshim DMA burst
    or spidev write) and READY wait, so the overlap is real.
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


def run_wall(toy, watchers, feed, args, use_pbo=False, playlist=None, index=0):
    """Render + pack + ship frames to the rp2350b over the link.

    The render readback is LINEAR (args.gamma left at 1.0) and gets packed
    into bit-planes (hub75.pack, byte-identical to the firmware — the packer
    applies the CIE gamma LUT, mirroring firmware lut.rs) before going out over
    the transport (4-lane parallel PIO bus by default, SPI fallback). The READY
    handshake self-paces: out.send() blocks until the rp2350b has armed its RX
    DMA, then pushes one 64 KB transfer.

    With a `playlist` (--loop), every args.loop seconds the current shader is
    torn down and the next compilable shader in the folder is built fresh —
    `index` tracks the position.  The feed (audio state + UDP socket) and the
    hardware link persist across switches; only the toy is swapped.
    """
    from .hub75 import pack, pack_single, to_single_chain

    # Warm the full render+pack path before opening hardware.
    if feed:
        feed.update(0.0, 1.0 / 60)
    warm = toy.render(0.0, 1.0 / 60, 0)
    if config.SINGLE_CHAIN:
        pack_single(to_single_chain(warm))
    else:
        pack(warm)

    # Transport: the 4-lane RP1-PIO parallel bus (default) or the 1-lane SPI
    # fallback. Both expose send(bytes)/close(); the byte stream is identical.
    if args.transport == "pio":
        from .pio_out import PioOut
        out = PioOut(clkdiv=args.pio_clkdiv, ready_bcm=args.ready_gpio,
                     nibble_swap=not args.pio_no_nibble_swap, use_cs=args.pio_cs)
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
    # The shader clock (iTime/iFrame) is per-shader so each --loop switch
    # restarts at 0; t0 stays global for --duration and the (continuous) feed.
    shader_t0 = t0
    shader_frame = 0
    switch_t = t0
    # Per-stage accumulators. render+pack run on this thread; the SPI transfer
    # runs on the worker (pipe.acc_send). `acc_wait` is how long this thread
    # blocks waiting for the previous transfer — the residual send-bound stall
    # AFTER overlap (≈0 => the link is fully hidden behind render).
    acc_render = acc_pack = acc_wait = 0.0
    pipe.acc_send = 0.0
    last_bytes = 0
    print(
        f"\n"
        f"  fps "
        f" render "
        f"  pack "
        f"  send "
        f"  wait"
    )
    try:
        while True:
            now = time.perf_counter()
            if args.duration and now - t0 >= args.duration:
                break
            # --loop: time to advance to the next shader in the folder.
            if playlist and now - switch_t >= args.loop:
                switch_t = now
                nxt = _next_compilable(playlist, index, args, use_pbo)
                if nxt is not None:
                    index, new_toy, watchers = nxt
                    toy.destroy()              # free the outgoing toy's GL objects
                    toy = new_toy
                    feed.channels = toy.audio_channels  # repoint the live feed
                    shader_t0, shader_frame = now, 0    # restart this shader's clock
                    print(f"--loop: now showing {os.path.basename(playlist[index])}")
            maybe_reload(toy, watchers)
            if feed:
                feed.update(now - t0, now - last)
            ta = time.perf_counter()
            # (H,W,3) uint8 LINEAR; shader clock is per-shader (resets on switch)
            buf = toy.render(now - shader_t0, now - last, shader_frame)
            # Physical-install orientation (see config): the wall is rotated 180deg
            # from the rendered frame, so flip both axes before packing.
            if config.FLIP_V:
                buf = buf[::-1]
            if config.FLIP_H:
                buf = buf[:, ::-1]
            buf = np.ascontiguousarray(buf)
            # Single-chain rig: fold the logical wall into the 512-wide serpentine
            # strip (chain A) before packing. pack() infers the wider frame.
            if config.SINGLE_CHAIN:
                buf = to_single_chain(buf)
            tb = time.perf_counter()
            payload = pack_single(buf) if config.SINGLE_CHAIN else pack(buf)
            tc = time.perf_counter()
            # Hand the frame to the worker; it transfers while we render the next.
            # submit() blocks only if the previous transfer hasn't finished.
            wait = pipe.submit(payload)   # fresh immutable bytes => no aliasing
            acc_render += tb - ta         # GLSL render + readback + flips + fold
            acc_pack += tc - tb           # bit-plane packing
            acc_wait += wait              # stall on the previous send (overlap residue)
            last_bytes = len(payload)
            last = now
            shader_frame += 1

            fps_frames += 1

            if now - fps_t >= 5.0:
                n = fps_frames
                # send = the worker's actual transfer time (link cost); wait =
                # how much it leaked into the critical path. If wait hugs 0 the
                # link is fully hidden and `render` is the clamp; if wait ~ send,
                # the link still paces. The floor is the theoretical transfer min.
                if args.transport == "pio":
                    # 4 lanes, 1 nibble/SM-cycle, 2 SM cycles/byte off RP1's 200 MHz.
                    floor_ms = last_bytes / (200e6 / (2 * args.pio_clkdiv)) * 1e3
                    link = f"PIO floor {floor_ms:4.1f}ms @ clkdiv {args.pio_clkdiv:g}"
                else:
                    floor_ms = last_bytes * 8 / args.spi_hz * 1e3
                    link = f"SPI floor {floor_ms:4.1f}ms @ {args.spi_hz/1e6:.0f}MHz"
                send_ms = pipe.acc_send / n * 1e3
                print(
                    f"{n / (now - fps_t):5.1f} "
                    f"{acc_render / n * 1e3:5.1f}ms "
                    f"{acc_pack / n * 1e3:4.1f}ms "
                    f"{send_ms:4.1f}ms "
                    f"{acc_wait / n * 1e3:4.1f}ms "
                    f"  ({link})"
                )
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
    ap.add_argument("--fps", type=float, default=120.0,
                    help="target fps cap (default 120)")
    ap.add_argument("--scale", type=int, default=2,
                    help="supersample factor (default 2; 1 = pixel-exact, 4 = "
                         "smoother but ~4x the GPU+readback cost)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="readback gamma (default 1.0 = LINEAR; the packer "
                         "applies the CIE LUT downstream, so correcting here "
                         "too would double-correct)")
    ap.add_argument("--transport", choices=("spi", "pio"), default="pio",
                    help="link to the rp2350b: 'pio' (4-lane RP1-PIO parallel "
                         "bus, default — needs phase6 firmware + "
                         "piobridge/libpioshim.so) or 'spi' (1-lane fallback)")
    ap.add_argument("--spi-hz", type=int, default=24_000_000,
                    help="SPI clock in Hz (--transport spi; start low, then ramp)")
    ap.add_argument("--pio-clkdiv", type=float, default=3.0,
                    help="RP1-PIO clock divisor (--transport pio); per-lane rate "
                         "≈ 200MHz/(2*div). Start high (slow), then lower "
                         "(default 3)")
    ap.add_argument("--pio-no-nibble-swap", action="store_true",
                    help="(--transport pio) disable the per-byte nibble swap — "
                         "use only if the logic analyzer shows nibbles arriving "
                         "un-swapped")
    ap.add_argument("--pio-cs", action="store_true",
                    help="(--transport pio) drive the optional CS frame line "
                         "(GPIO21→GP25 jumper); requires firmware built with "
                         "USE_CS=true. Default off (READY-framed)")
    ap.add_argument("--ready-gpio", type=int, default=25,
                    help="BCM pin reading the rp2350b READY line")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (default: run forever)")
    ap.add_argument("--loop", type=float, default=None, metavar="SECONDS",
                    help="cycle through every .glsl in the launched shader's "
                         "folder, switching every SECONDS and wrapping at the "
                         "end (buffer siblings .bufA-D.glsl are skipped). Each "
                         "shader restarts fresh (iTime/iFrame=0, buffers "
                         "cleared). Hardware only")
    ap.add_argument("--dry-run", nargs="?", const=120, type=int, default=None,
                    metavar="N", help="headless: render N frames (default "
                    "120), save a GIF, no hardware")
    ap.add_argument("--out", default="/tmp/shadertoy_out.gif",
                    help="dry-run GIF path (default /tmp/shadertoy_out.gif)")
    ap.add_argument("--width", type=int, default=None,
                    help="render width (default: %d)" % config.WALL_WIDTH)
    ap.add_argument("--height", type=int, default=None,
                    help="render height (default: %d)" % config.WALL_HEIGHT)
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
    # readback is LINEAR (gamma 1.0) because the packer owns the CIE gamma LUT
    # (config.PACK_GAMMA) — applying gamma here too would double-correct.
    if args.width is None:
        args.width = config.WALL_WIDTH
    if args.height is None:
        args.height = config.WALL_HEIGHT

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
    dry = args.dry_run is not None
    toy, watchers = build_shader(args, use_pbo, args.shader, fatal=True)

    feed = AudioFeed(toy.audio_channels,
                     allow_listen=not args.no_listen and not dry)

    # --loop: build the folder playlist and find where the launched shader sits.
    # Ignored in dry-run (the GIF is a single shader). One-shader folders just
    # run normally.
    playlist, index = None, 0
    if args.loop is not None and not dry:
        playlist = build_playlist(args.shader)
        start = os.path.abspath(args.shader)
        index = playlist.index(start) if start in playlist else 0
        if len(playlist) <= 1:
            print(f"--loop: only one shader in {os.path.dirname(start)} — "
                  "nothing to cycle")
            playlist = None
        else:
            print(f"--loop: cycling {len(playlist)} shaders every "
                  f"{args.loop:g}s")

    if dry:
        run_dry(toy, feed, args)
    else:
        run_wall(toy, watchers, feed, args,
                 use_pbo=use_pbo, playlist=playlist, index=index)


if __name__ == "__main__":
    main()
