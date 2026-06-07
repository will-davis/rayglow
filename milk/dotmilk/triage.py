"""Triage: headless-render every .milk preset and grade the result.

For each preset: run N simulated frames at 60fps with synth features, snapshot
3 frames, classify health, then emit contact-sheet PNGs + a ranked report.

Run (no root):
    ~/rgbvenv/bin/python -m milk.dotmilk.triage milk/presets/dotmilk-presets \\
        -o /tmp/milk-triage [--frames 360] [--limit 50]

Classification:
    ok         alive, moving, reasonable brightness
    dark       buffer essentially black by the end
    static     image stops changing (motionless preset at LED scale)
    saturated  buffer pinned ~white
    slow       below 60 fps headless (will drop frames on hardware)
    error      equations raised during execution
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .. import config
from ..engine import Engine

SCALE = 2          # thumbnail upscale
SNAP_AT = (1 / 3, 2 / 3, 1.0)   # fractions of the run to snapshot
PER_SHEET = 24     # presets per contact sheet
TIME_BUDGET = 12.0 # seconds of wall clock per preset before grading a partial run


def triage_one(path, frames):
    eng = Engine(preset_name="tunnel", listen=False)
    try:
        eng.set_preset(str(path))
    except Exception as e:
        return {"name": Path(path).stem, "class": "error",
                "detail": f"load: {e}", "snaps": None, "fps": 0.0}

    dt = 1.0 / 60.0
    snaps = []
    snap_frames = {max(1, int(frames * f)) for f in SNAP_AT}
    prev = None
    motion = 0.0
    aborted = False
    t0 = time.perf_counter()
    i = 0
    try:
        for i in range(1, frames + 1):
            frame = eng.step(now=i * dt, dt=dt)
            if i in snap_frames or i >= frames - 30:
                if prev is not None and (i == frames or aborted):
                    pass
                if i >= frames - 30:
                    if prev is not None:
                        motion = float(np.abs(frame.astype(np.int16) - prev).mean())
                    prev = frame.copy()
            if i in snap_frames:
                snaps.append(frame.copy())
            if i % 30 == 0 and time.perf_counter() - t0 > TIME_BUDGET:
                aborted = True            # pathologically slow: grade on partial run
                if prev is None:
                    prev = frame.copy()
                motion = max(motion, 1.0)  # it ran; don't misclass as static
                snaps.append(frame.copy())
                break
    except Exception as e:
        return {"name": eng.preset.name, "class": "error",
                "detail": f"step: {e}", "snaps": snaps or None, "fps": 0.0}
    fps = max(i, 1) / (time.perf_counter() - t0)

    buf = eng.fb.buf
    mean = float(buf.mean())
    detail = f"mean={mean:.3f} motion={motion:.2f} fps={fps:.0f}"
    if eng.preset.errors:
        cls = "error"
        detail += f" | {eng.preset.errors[0][:60]}"
    elif not np.isfinite(buf).all():
        cls = "error"
        detail += " | NaN in buffer"
    elif mean < 0.004:
        cls = "dark"
    elif mean > 0.90:
        cls = "saturated"
    elif motion < 0.05:
        cls = "static"
    elif fps < 60:
        cls = "slow"
    else:
        cls = "ok"
    return {"name": eng.preset.name, "class": cls, "detail": detail,
            "snaps": snaps, "fps": fps}


def make_sheets(results, outdir):
    W, H = config.WIDTH, config.HEIGHT
    tile_w, tile_h = W * SCALE, H * SCALE
    label_h = 14
    from PIL import ImageDraw
    sheet_idx = 0
    for start in range(0, len(results), PER_SHEET):
        chunk = results[start:start + PER_SHEET]
        img = Image.new("RGB", (tile_w, len(chunk) * (tile_h + label_h + 4)), (12, 12, 12))
        dr = ImageDraw.Draw(img)
        y = 0
        for r in chunk:
            dr.text((2, y + 1), f"[{r['class']}] {r['name'][:70]}", fill=(180, 180, 180))
            y += label_h
            if r["snaps"]:
                # show the last snapshot full-width (most representative)
                tile = Image.fromarray(r["snaps"][-1], "RGB").resize((tile_w, tile_h), Image.NEAREST)
                img.paste(tile, (0, y))
            y += tile_h + 4
        out = Path(outdir) / f"sheet_{sheet_idx:02d}.png"
        img.save(out)
        sheet_idx += 1
    return sheet_idx


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir", help="directory of .milk files")
    ap.add_argument("-o", "--out", default="/tmp/milk-triage")
    ap.add_argument("--frames", type=int, default=360, help="frames per preset (360 = 6s)")
    ap.add_argument("--limit", type=int, default=None, help="only first N presets")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("*.milk"))
    if args.limit:
        files = files[:args.limit]
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    results = []
    t0 = time.perf_counter()
    for i, f in enumerate(files, 1):
        r = triage_one(f, args.frames)
        results.append(r)
        print(f"[{i}/{len(files)}] {r['class']:9s} {r['name'][:60]}", flush=True)

    counts = {}
    for r in results:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    elapsed = time.perf_counter() - t0

    order = {"ok": 0, "slow": 1, "static": 2, "saturated": 3, "dark": 4, "error": 5}
    results_sorted = sorted(results, key=lambda r: (order[r["class"]], r["name"].lower()))
    n_sheets = make_sheets(results_sorted, outdir)

    # keepers playlist: pass directly to `-m milk --milk <outdir>/keepers.txt`
    # slow presets stay only if they hold >=30 fps headless (hardware adds contention)
    keepers = [r for r in results
               if r["class"] in ("ok", "static")
               or (r["class"] == "slow" and r["fps"] >= 30.0)]
    with open(outdir / "keepers.txt", "w") as fp:
        for r in keepers:
            fp.write(str(Path(args.dir).resolve() / f"{r['name']}.milk") + "\n")

    with open(outdir / "report.txt", "w") as fp:
        fp.write(f"milk triage: {len(files)} presets, {args.frames} frames each, "
                 f"{elapsed:.0f}s total\n")
        fp.write("".join(f"  {k}: {v}\n" for k, v in sorted(counts.items())))
        fp.write("\n")
        for r in results_sorted:
            fp.write(f"{r['class']:9s}  {r['name'][:70]:70s}  {r['detail']}\n")

    print(f"\n{counts}  ({elapsed:.0f}s)")
    print(f"wrote {n_sheets} sheets + report.txt + keepers.txt ({len(keepers)}) to {outdir}")


if __name__ == "__main__":
    main()
