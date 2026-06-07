"""Shadertoy renderer entry point.

Headless dry-run (no root, no rgbmatrix import; writes an animated GIF):
    ~/rgbvenv/bin/python -m shadertoy ../example.glsl --dry-run 120

Hardware (user runs this; root needed for GPIO):
    sudo ~/rgbvenv/bin/python -m shadertoy ../example.glsl

Texture/audio channels — bind iChannel0..3 with --channelN flags or, better,
`// iChannelN: spec` comment directives inside the shader file:
    sudo ~/rgbvenv/bin/python -m shadertoy presets/foo.glsl \\
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
import time

import numpy as np

from ..feed import config  # geometry/gamma source of truth (shared feed pkg)

from .egl import GLContext, GLError
from .pipeline import ShaderToy
from .reload import GlslWatcher


def pin_to_core(core):
    """Pin the render thread off core 3 (owned by the hzeller GPIO thread).
    Per-thread on Linux, so the matrix update thread is unaffected."""
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


def run_matrix(toy, watchers, feed, args):
    # Everything filesystem/import-shaped happens BEFORE RGBMatrix().  We
    # also set drop_privileges=0 (we keep root) so hot reload can re-read
    # the .glsl afterwards — /home/will is unreadable post-drop otherwise.
    from PIL import Image
    if feed:
        feed.update(0.0, 1.0 / 60)             # warm np.fft path too
    Image.fromarray(toy.render(0.0, 1.0 / 60, 0), "RGB")  # warm full path

    from rgbmatrix import RGBMatrix
    options = config.matrix_options()
    options.drop_privileges = 0
    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()        # create ONCE, reuse forever
    pin_to_core(config.RENDER_CORE)            # after init

    frame_interval = 1.0 / args.fps
    t0 = time.perf_counter()
    last = t0
    fps_frames, fps_t = 0, t0
    frame = 0
    try:
        while True:
            now = time.perf_counter()
            if args.duration and now - t0 >= args.duration:
                break
            maybe_reload(toy, watchers)
            if feed:
                feed.update(now - t0, now - last)
            buf = toy.render(now - t0, now - last, frame)
            last = now
            frame += 1
            canvas.SetImage(Image.fromarray(buf, "RGB"))
            canvas = matrix.SwapOnVSync(canvas)

            fps_frames += 1
            if now - fps_t >= 5.0:
                print(f"{fps_frames / (now - fps_t):6.1f} fps")
                fps_frames, fps_t = 0, now
            # SwapOnVSync paces to the matrix refresh; additionally cap to
            # --fps so we don't render frames nobody asked for.
            sleep = frame_interval - (time.perf_counter() - now)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        pass
    finally:
        matrix.Clear()


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
    ap.add_argument("--gamma", type=float, default=config.GAMMA,
                    help=f"composite gamma (default {config.GAMMA})")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (default: run forever)")
    ap.add_argument("--dry-run", nargs="?", const=120, type=int, default=None,
                    metavar="N", help="headless: render N frames (default "
                    "120), save a GIF, no hardware")
    ap.add_argument("--out", default="/tmp/shadertoy_out.gif",
                    help="dry-run GIF path (default /tmp/shadertoy_out.gif)")
    ap.add_argument("--width", type=int, default=config.WIDTH)
    ap.add_argument("--height", type=int, default=config.HEIGHT)
    for i in range(4):
        ap.add_argument(f"--channel{i}", metavar="SPEC", default=None,
                        help=("iChannel0 source: 'audio', 'milk', "
                              "'noise[:seed[:size]]', or an image path "
                              "(likewise --channel1..3)"
                              if i == 0 else argparse.SUPPRESS))
    ap.add_argument("--no-listen", action="store_true",
                    help="audio channel: never bind the UDP socket, "
                         "synth fallback only")
    args = ap.parse_args()

    try:
        ctx = GLContext()
    except GLError as e:
        print(f"GL init failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"GPU: {ctx.info()}")

    toy = ShaderToy(args.width, args.height, scale=args.scale,
                    gamma=args.gamma,
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
        run_matrix(toy, watchers, feed, args)


if __name__ == "__main__":
    main()
