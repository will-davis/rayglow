"""Milk-Pi entry point.

Headless benchmark (no root, no rgbmatrix import):
    ~/rgbvenv/bin/python -m milk --headless [N_FRAMES] --preset tunnel

Hardware (user runs this; root needed for GPIO):
    sudo ~/rgbvenv/bin/python -m milk --preset tunnel

Real MilkDrop presets (file or directory playlist; n/p/space/r/q keys):
    sudo ~/rgbvenv/bin/python -m milk --milk milk/presets/dotmilk-presets --duration 20

Feed it features (separate terminal, or later from will-desktop):
    ~/rgbvenv/bin/python milk/fake_sender.py
"""
import argparse
import os
import sys
import time

import numpy as np

from . import config
from .engine import Engine
from .presets import PRESETS


def pin_to_core(core):
    """Pin the calling thread (only) off core 3, which the hzeller GPIO
    bit-bang thread owns.  os.sched_setaffinity(0,...) is per-thread on Linux,
    so the matrix update thread is unaffected."""
    try:
        os.sched_setaffinity(0, {core})
    except OSError as e:
        print(f"warning: could not pin to core {core}: {e}", file=sys.stderr)


def run_headless(engine, frames):
    """Unthrottled benchmark + numerics check.  Exits nonzero on NaN or a
    dead/blown-up buffer."""
    print(f"headless: {frames} frames at {config.WIDTH}x{config.HEIGHT}, "
          f"preset steering live={engine.receiver is not None}")
    ok = True
    t0 = time.perf_counter()
    last = t0
    for i in range(frames):
        now = time.perf_counter()
        engine.step(now - t0, now - last)
        last = now
        if (i + 1) % 60 == 0:
            buf = engine.fb.buf
            bmin, bmean, bmax = float(buf.min()), float(buf.mean()), float(buf.max())
            if not np.isfinite(buf).all():
                print(f"frame {i+1}: NaN/inf in buffer!")
                ok = False
                break
            print(f"frame {i+1:5d}: buf min={bmin:.4f} mean={bmean:.4f} "
                  f"max={bmax:.4f}  stages(ms) " +
                  " ".join(f"{k}={v:.3f}" for k, v in engine.stage_ms.items()))
    elapsed = time.perf_counter() - t0
    fps = frames / elapsed
    print(f"\n{frames} frames in {elapsed:.2f}s = {fps:.0f} fps "
          f"({1000.0/fps:.2f} ms/frame) — target 60 fps "
          f"({'OK' if fps > 60 else 'TOO SLOW'})")

    buf = engine.fb.buf
    if buf.max() <= 1e-4:
        print("FAIL: buffer decayed to black — feedback loop is dead")
        ok = False
    if buf.mean() > 0.98:
        print("FAIL: buffer pinned at max — feedback loop is blowing up")
        ok = False
    if ok:
        print("numerics OK: finite, alive, not saturated")
    sys.exit(0 if ok else 1)


def run_matrix(engine, playlist=None):
    # Warm up the full render path BEFORE RGBMatrix() drops root privileges:
    # post-drop, /home/will is unreadable — lazy imports, font/file loads AND
    # .milk preset loads all fail.  So: warm imports, then preload the entire
    # playlist (parse+compile every .milk) while the filesystem is readable.
    engine.warmup()
    from PIL import Image
    Image.fromarray(engine.fb.composite(), "RGB")  # warm PIL path too
    kb = None
    if playlist is not None:
        from .playlist import Keyboard             # import before priv drop
        playlist.preload()
        playlist.tick(engine, 0.0, None)           # activate first preset
    # warm the ACTIVE preset's full draw path too (waves/custom/etc. modules
    # must be imported now — post-drop module loads fail)
    engine.step(0.016, 0.016)
    engine.step(0.033, 0.016)
    engine.fb.reset()

    from rgbmatrix import RGBMatrix
    matrix = RGBMatrix(options=config.matrix_options())
    canvas = matrix.CreateFrameCanvas()            # create ONCE, reuse forever
    pin_to_core(config.RENDER_CORE)                # after init: GPIO thread owns core 3

    if playlist is not None:
        kb = Keyboard()

    t0 = time.perf_counter()
    last = t0
    fps_frames = 0
    fps_t = t0
    try:
        while True:
            now = time.perf_counter()
            if playlist is not None:
                if not playlist.tick(engine, now - t0, kb.poll() if kb else None):
                    break
            frame = engine.step(now - t0, now - last)
            last = now
            canvas.SetImage(Image.fromarray(frame, "RGB"))
            canvas = matrix.SwapOnVSync(canvas)    # blocks until vsync: our pacing

            fps_frames += 1
            if now - fps_t >= 5.0:
                live = "live" if engine.features.live else "fallback-synth"
                print(f"{fps_frames / (now - fps_t):6.1f} fps  ({live})")
                fps_frames = 0
                fps_t = now
    except KeyboardInterrupt:
        pass
    finally:
        if kb is not None:
            kb.restore()
        matrix.Clear()


def main():
    ap = argparse.ArgumentParser(prog="milk", description=__doc__)
    ap.add_argument("--preset", default="tunnel", choices=sorted(PRESETS))
    ap.add_argument("--milk", metavar="PATH",
                    help=".milk preset file, or a directory to rotate through")
    ap.add_argument("--duration", type=float, default=20.0,
                    help="seconds per preset in playlist rotation (default 20)")
    ap.add_argument("--shuffle", action="store_true", help="shuffle the playlist")
    ap.add_argument("--headless", nargs="?", const=600, type=int, default=None,
                    metavar="N", help="benchmark N frames without hardware (default 600)")
    ap.add_argument("--no-listen", action="store_true",
                    help="skip the UDP receiver (pure synth fallback)")
    ap.add_argument("--gamma", type=float, default=None,
                    help=f"override composite gamma (default {config.GAMMA})")
    args = ap.parse_args()

    if args.gamma is not None:
        config.GAMMA = args.gamma

    engine = Engine(preset_name=args.preset, listen=not args.no_listen,
                    profile=args.headless is not None)
    playlist = None
    if args.milk:
        import os.path
        if os.path.isfile(args.milk) and args.milk.endswith(".milk"):
            engine.set_preset(args.milk)
        else:
            from .playlist import Playlist
            playlist = Playlist(args.milk, duration=args.duration, shuffle=args.shuffle)

    if args.headless is not None:
        pin_to_core(config.RENDER_CORE)
        if playlist is not None:
            playlist.tick(engine, 0.0, None)
        run_headless(engine, args.headless)
    else:
        run_matrix(engine, playlist)


if __name__ == "__main__":
    main()
