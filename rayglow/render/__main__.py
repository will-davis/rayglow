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
listens.  'milk' is a 16x3 float texture of the v3 feed: 8 log-spaced bands
(1.0 = typical, hits spike 2-3) with three flywheel envelopes and three
"music time" theta phases each, per-band onsets, beat/key/descriptor/stereo
globals, packet liveness, and the legacy MilkDrop scalars — see MilkChannel
in textures.py for the texel map.  'spectrum' is a 128x1 float texture: the
feed's real spectrum (.x) + smooth band curves (.y/.z).  Use milk when the
audio texture's clamped spectrum feels binary, spectrum for a real shape.

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

While running on hardware the renderer also opens a TCP control plane
(render/control.py, port config.CONTROL_PORT=5006; --no-control to skip) — an
mpd-style command channel.  Drive it with tools/rayglow_ctl.py from the Pi or
the desktop:
    rayglow-ctl push foo.glsl    # ship a local edit NOW (skips the file sync)
    rayglow-ctl load ~/presets/bar.glsl   # switch to a shader already on the Pi
    rayglow-ctl next / prev / play / pause / reload
    rayglow-ctl loop 30 | loop off | repeat | status
    rayglow-ctl scale 3 | scale auto      # live supersample change (rebuilds)
`push` is the low-latency dev loop: it hands the source (plus buffer siblings
and referenced image assets) straight to the running renderer, so the wall
updates in <100ms instead of waiting on mutagen, and GLSL compile errors come
back in the reply.  play/pause freeze the shader clock (the audio feed keeps
running); loop/repeat/next/prev are the playlist controls over the launched
shader's folder.  See tools/nvim-rayglow.lua for the save-hook.

Supersample scale is per-shader and live: precedence is --scale > a shader's
`// rayglow: scale=N` directive > config.DEFAULT_SCALE.  `scale N` sets a
runtime override (sticky across switches until `scale auto` clears it back to
the directive/default); each change reallocates FBOs, so it rebuilds the shader
(iTime restarts).
"""
import argparse
import base64
import os
import queue
import re
import sys
import threading
import time

import numpy as np

from ..feed import config  # geometry/gamma source of truth (shared feed pkg)

from . import textures
from .control import ControlServer, PlayerState
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


def _directive_scale(image_src):
    """The `// rayglow: scale=N` directive from a shader source, validated to a
    1..8 int, or None if absent/malformed."""
    val = textures.parse_settings(image_src).get("scale")
    if val is None:
        return None
    try:
        n = int(val)
    except ValueError:
        print(f"shader setting: ignoring non-integer scale={val!r}",
              file=sys.stderr)
        return None
    if not 1 <= n <= 8:
        print(f"shader setting: clamping scale={n} to [1,8]", file=sys.stderr)
        n = max(1, min(8, n))
    return n


def resolve_scale(scale_override, directive_scale):
    """Effective supersample factor + where it came from.  Precedence: an
    explicit override (CLI --scale, seeded into PlayerState, or a runtime
    `scale` command) wins; else the shader's `// rayglow: scale=` directive;
    else config.DEFAULT_SCALE.  Returns (scale, source)."""
    if scale_override is not None:
        return scale_override, "override"
    if directive_scale is not None:
        return directive_scale, "directive"
    return config.DEFAULT_SCALE, "default"


def build_shader(args, use_pbo, shader_path, fatal=True, scale_override=None):
    """Build a fresh ShaderToy + GlslWatchers for one shader file and its
    sibling buffer passes (foo.bufA.glsl .. foo.bufD.glsl).

    All passes are created before any compile so buffer cross-references
    resolve regardless of order; buffers compile first, image last.  CLI
    --channelN overrides apply to the image pass.

    Scale (supersample factor) is resolved per build: `scale_override` (the CLI
    --scale seed or a runtime `scale` command) wins, else the image shader's
    `// rayglow: scale=N` directive, else config.DEFAULT_SCALE.  A scale change
    reallocates FBOs, which is exactly what this fresh build does — so runtime
    scale rides the same rebuild path as a shader switch (there is no in-place
    scale change).

    args.render_gamma / args.resolve_flip are derived in main(): the resolve
    pass bakes them in on the GPU (wall runs get PACK_GAMMA + config flips;
    dry-runs get --gamma and no flips, as before); legacy mode instead feeds
    --gamma to the CPU readback LUT.

    fatal=True (initial launch): a compile error exits the process.
    fatal=False (--loop switch / control switch): a compile error prints, tears
    the half-built toy back down, and returns (None, None, msg) so the caller
    keeps the last good shader and can relay `msg` to a control client.

    Returns (toy, watchers, err): err is None on success, else the compile
    message (fatal=False only).
    """
    # Read the image source up front so the `// rayglow: scale=` directive can
    # size the FBOs (ShaderToy allocates at scale on construction).
    image_watcher = GlslWatcher(shader_path)
    try:
        image_src = image_watcher.read()
    except OSError:
        image_src = ""
    scale, scale_src = resolve_scale(scale_override, _directive_scale(image_src))
    if scale_src != "default":
        print(f"scale: {scale}x ({scale_src})")

    toy = ShaderToy(args.width, args.height, scale=scale,
                    gamma=args.render_gamma, use_pbo=use_pbo,
                    readback=args.readback, resolve_flip=args.resolve_flip,
                    quiet=not fatal,
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
    watchers["image"] = image_watcher   # reuse the early read; keeps image last
    if len(watchers) > 1:
        print(f"multipass: {', '.join(watchers)} "
              f"(buffers: {toy.buffer_format[3]})")
    for name, watcher in watchers.items():
        if fatal:
            compile_or_die(toy, name, watcher.read())
        else:
            ok, msg = toy.set_source(name, watcher.read())
            if not ok:
                print(f"build: {shader_path} compile error "
                      f"({name}):\n{msg}", file=sys.stderr)
                toy.destroy()
                return None, None, f"compile error ({name}): {msg}"
            if msg:
                print(f"warning ({name}): {msg}", file=sys.stderr)
    return toy, watchers, None


def build_playlist(shader_path):
    """Sorted absolute paths of the standalone .glsl files in shader_path's
    directory.  Buffer siblings (foo.bufA.glsl .. foo.bufD.glsl) are excluded
    — they're multipass inputs, not shaders to display on their own."""
    d = os.path.dirname(os.path.abspath(shader_path))
    buf_re = re.compile(r"\.buf[A-D]\.glsl$")
    names = sorted(f for f in os.listdir(d)
                   if f.endswith(".glsl") and not buf_re.search(f))
    return [os.path.join(d, n) for n in names]


def switch_to(old_toy, feed, player, args, use_pbo, path, display_name=None):
    """Full rebuild -> hot-swap: build a fresh toy for `path`, and on success
    destroy the outgoing one, repoint the live audio feed at the new channels,
    and reset the (pausable) shader clock to 0.  Playlist/index are the caller's
    to manage.  Returns (new_toy, new_watchers, None) on success, or
    (None, None, err) on failure with `old_toy` left untouched (last good
    shader stays on the wall)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        return None, None, f"no such shader: {path}"
    new_toy, new_watchers, err = build_shader(args, use_pbo, path, fatal=False,
                                              scale_override=player.scale_override)
    if new_toy is None:
        return None, None, err
    old_toy.destroy()                       # free the outgoing toy's GL objects
    feed.channels = new_toy.audio_channels  # repoint the live feed
    player.current_path = path
    player.display_name = display_name or os.path.basename(path)
    player.scale = new_toy.scale             # effective scale after precedence
    player.shader_time = 0.0                 # full rebuild => iTime/iFrame = 0
    player.shader_frame = 0
    return new_toy, new_watchers, None


def _advance(toy, feed, player, args, use_pbo, step):
    """Walk the folder playlist by `step` (+1 next / -1 prev, wrapping) to the
    next shader that compiles and switch to it, updating player.index.  Shared
    by --loop auto-advance and the next/prev commands.  Returns
    (new_toy, new_watchers, None) or (None, None, err) (keep current)."""
    pl = player.playlist
    n = len(pl)
    if n == 0:
        return None, None, "playlist empty"
    start = player.index if player.index >= 0 else 0
    for k in range(1, n + 1):
        j = (start + step * k) % n
        nt, nw, _err = switch_to(toy, feed, player, args, use_pbo, pl[j])
        if nt is not None:
            player.index = j
            return nt, nw, None
    return None, None, "no compilable shader in folder"


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


_BUF_KEY = re.compile(r"buf[A-D]")


def _safe_rel(rel):
    """Sanitize a client-supplied asset path: drop absolute/`..` components so a
    push can't write outside LIVE_DIR."""
    parts = [p for p in rel.replace("\\", "/").split("/")
             if p and p not in (".", "..")]
    return os.path.join(*parts) if parts else ""


def _write_live_slot(msg):
    """Write a pushed bundle into LIVE_DIR (the tmp file that holds what's
    running) and return the image .glsl path.  Clears this shader's stale buffer
    siblings first, so a bufX removed in the editor doesn't linger and get
    auto-discovered on the rebuild.  `msg`: {name, passes{image,bufA..D},
    assets?{relpath: base64}}."""
    passes = msg.get("passes") or {}
    if "image" not in passes:
        raise ValueError("push: bundle has no image pass")
    live = os.path.expanduser(config.LIVE_DIR)
    os.makedirs(live, exist_ok=True)
    name = os.path.basename(msg.get("name") or "now-playing.glsl")  # no traversal
    if not name.endswith(".glsl"):
        name += ".glsl"
    base = re.sub(r"\.glsl$", "", name)
    for x in "ABCD":
        stale = os.path.join(live, f"{base}.buf{x}.glsl")
        if os.path.exists(stale):
            os.remove(stale)
    image_path = os.path.join(live, name)
    with open(image_path, "w") as f:
        f.write(passes["image"])
    for key, src in passes.items():
        if key == "image":
            continue
        if not _BUF_KEY.fullmatch(key):
            raise ValueError(f"push: bad pass name {key!r}")
        with open(os.path.join(live, f"{base}.{key}.glsl"), "w") as f:
            f.write(src)
    for rel, b64 in (msg.get("assets") or {}).items():
        safe = _safe_rel(rel)
        if not safe:
            continue
        dst = os.path.join(live, safe)
        os.makedirs(os.path.dirname(dst) or live, exist_ok=True)
        with open(dst, "wb") as f:
            f.write(base64.b64decode(b64))
    return image_path


def _reseat_playlist(player, path):
    """Rebuild the folder playlist around `path` and point index at it."""
    player.playlist = build_playlist(path)
    ap = os.path.abspath(path)
    player.index = player.playlist.index(ap) if ap in player.playlist else -1


def _run_command(msg, toy, watchers, feed, player, args, use_pbo):
    """Execute one control command on the render thread and return
    (toy, watchers, reply).  Switching commands reassign toy/watchers on
    success; everything else just mutates player state."""
    c = msg.get("cmd")
    if c == "status":
        return toy, watchers, {"ok": True, **player.snapshot()}
    if c == "play":
        player.paused = False
        return toy, watchers, {"ok": True, "paused": False}
    if c == "pause":
        player.paused = True
        return toy, watchers, {"ok": True, "paused": True}
    if c == "repeat":
        player.repeat = bool(msg["on"]) if "on" in msg else not player.repeat
        return toy, watchers, {"ok": True, "repeat": player.repeat}
    if c == "loop":
        if msg.get("off"):
            player.loop_interval = None
        else:
            secs = msg.get("seconds")
            if not isinstance(secs, (int, float)) or secs <= 0:
                return toy, watchers, {"ok": False,
                                       "error": "loop: need seconds > 0 or off"}
            player.loop_interval = float(secs)
        return toy, watchers, {"ok": True, "loop": player.loop_interval}
    if c == "scale":
        # Set the override (or clear it back to auto = defer to directive/
        # default), then rebuild the current shader at the new scale — an FBO
        # realloc, so it goes through the full-rebuild switch path.
        v = msg.get("value")
        if v in (None, "auto"):
            player.scale_override = None
        elif isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 8:
            player.scale_override = v
        else:
            return toy, watchers, {"ok": False,
                                   "error": "scale: an int 1..8 or 'auto'"}
        nt, nw, err = switch_to(toy, feed, player, args, use_pbo,
                                player.current_path,
                                display_name=player.display_name)
        if nt is None:
            return toy, watchers, {"ok": False, "error": err or "rebuild failed"}
        print(f"control: scale {player.scale}x"
              + ("" if player.scale_override is not None else " (auto)"))
        return nt, nw, {"ok": True, **player.snapshot()}
    if c in ("load", "reload", "push", "next", "prev"):
        if c == "load":
            nt, nw, err = switch_to(toy, feed, player, args, use_pbo,
                                    msg["path"])
            if nt is not None:
                _reseat_playlist(player, player.current_path)
        elif c == "reload":
            nt, nw, err = switch_to(toy, feed, player, args, use_pbo,
                                    player.current_path,
                                    display_name=player.display_name)
        elif c == "push":
            image_path = _write_live_slot(msg)
            nt, nw, err = switch_to(toy, feed, player, args, use_pbo,
                                    image_path, display_name=msg.get("name"))
            if nt is not None:
                _reseat_playlist(player, image_path)
        else:                                     # next / prev
            nt, nw, err = _advance(toy, feed, player, args, use_pbo,
                                   +1 if c == "next" else -1)
        if nt is None:
            return toy, watchers, {"ok": False, "error": err or "switch failed"}
        print(f"control: now showing {player.display_name}")
        return nt, nw, {"ok": True, **player.snapshot()}
    return toy, watchers, {"ok": False, "error": f"unknown command: {c!r}"}


def drain_commands(cmd_queue, toy, watchers, feed, player, args, use_pbo):
    """Execute every queued control command on the render thread and fill each
    reply.  Returns the possibly-new (toy, watchers).  A bad command can never
    kill the render loop — it's caught and reported to that client."""
    while True:
        try:
            cmd = cmd_queue.get_nowait()
        except queue.Empty:
            break
        try:
            toy, watchers, reply = _run_command(
                cmd.msg, toy, watchers, feed, player, args, use_pbo)
        except (OSError, ValueError, KeyError, TypeError) as e:
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        cmd.reply = reply
        cmd.done.set()
    return toy, watchers


def run_wall(toy, watchers, feed, args, player, cmd_queue, use_pbo=False):
    """Render + pack + ship frames to the rp2350b over the link.

    Gamma: the GPU resolve pass bakes pow(x, PACK_GAMMA) into the frame, so
    the packer gets an identity LUT (LUT_IDENTITY) — applying its CIE LUT too
    would double-correct.  In --readback legacy the readback is LINEAR and
    the packer applies the CIE LUT itself, exactly as before (that pairing is
    what tools/verify.py proves byte-identical to the firmware).  Orientation
    (config.FLIP_V/FLIP_H) is likewise baked into the resolve pass; only
    legacy mode still flips on the CPU.

    The frame goes out over the transport (4-lane parallel PIO bus by
    default, SPI fallback). The READY handshake self-paces: out.send() blocks
    until the rp2350b has armed its RX DMA, then pushes one 64 KB transfer.

    `player` (PlayerState) holds all playback state — the folder playlist,
    loop/repeat/pause, and the pausable shader clock.  Two things drive shader
    switches, both via switch_to/_advance: --loop auto-advance
    (player.loop_interval) and control commands drained from `cmd_queue` each
    frame (load/push/next/prev/reload).  The feed (audio state + UDP socket) and
    the hardware link persist across switches; only the toy is swapped.  Pause
    freezes the shader clock (iTime/iFrame) but not the feed — audio keeps
    flowing so a resume picks up live.
    """
    from .hub75 import (LUT_IDENTITY, build_gamma_lut, pack, pack_single,
                        to_single_chain)

    legacy = args.readback == "legacy"
    pack_lut = build_gamma_lut() if legacy else LUT_IDENTITY

    # Warm the full render+pack path before opening hardware.
    if feed:
        feed.update(0.0, 1.0 / 60)
    warm = toy.render(0.0, 1.0 / 60, 0)
    if config.SINGLE_CHAIN:
        pack_single(to_single_chain(warm), pack_lut)
    else:
        pack(warm, pack_lut)

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
    # The shader clock lives on `player` now: player.shader_time accumulates real
    # dt only while not paused (so pause freezes iTime/iFrame and resume doesn't
    # jump), and a switch resets it to 0.  t0 stays global for --duration and the
    # continuous feed.  switch_t paces --loop auto-advance.
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
            # Control plane: drain any queued commands (load/push/next/prev/...)
            # on this thread — GL is thread-affine, so the socket threads only
            # enqueue.  A switch resets switch_t so auto-advance doesn't fire on
            # top of a manual one.
            prev_toy = toy
            toy, watchers = drain_commands(cmd_queue, toy, watchers, feed,
                                           player, args, use_pbo)
            if toy is not prev_toy:
                switch_t = now
            # --loop auto-advance: only while looping, not held (repeat) or paused.
            if (player.loop_interval and not player.repeat and not player.paused
                    and len(player.playlist) > 1
                    and now - switch_t >= player.loop_interval):
                switch_t = now
                nt, nw, _err = _advance(toy, feed, player, args, use_pbo, +1)
                if nt is not None:
                    toy, watchers = nt, nw
                    print(f"loop: now showing {player.display_name}")
            maybe_reload(toy, watchers)
            if feed:
                feed.update(now - t0, now - last)
            ta = time.perf_counter()
            # (H,W,3) uint8. The shader clock is pausable and per-shader (resets
            # on switch); pass dt=0 while paused so iTimeDelta freezes too.
            # Resolve-pass modes return gamma-corrected, already-oriented frames;
            # legacy returns LINEAR unflipped ones.
            shader_dt = 0.0 if player.paused else now - last
            buf = toy.render(player.shader_time, shader_dt, player.shader_frame)
            if not player.paused:
                player.shader_time += now - last
                player.shader_frame += 1
            if legacy:
                # Physical-install orientation (see config): flip on the CPU;
                # the resolve pass does this on the GPU in the other modes.
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
            payload = (pack_single(buf, pack_lut) if config.SINGLE_CHAIN
                       else pack(buf, pack_lut))
            tc = time.perf_counter()
            # Hand the frame to the worker; it transfers while we render the next.
            # submit() blocks only if the previous transfer hasn't finished.
            wait = pipe.submit(payload)   # fresh immutable bytes => no aliasing
            acc_render += tb - ta         # GLSL render + readback + flips + fold
            acc_pack += tc - tb           # bit-plane packing
            acc_wait += wait              # stall on the previous send (overlap residue)
            last_bytes = len(payload)
            last = now

            fps_frames += 1

            if now - fps_t >= 5.0:
                n = fps_frames
                player.fps = n / (now - fps_t)   # published via `status`
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
    ap.add_argument("--scale", type=int, default=None,
                    help="supersample factor. Precedence: this flag > a shader's "
                         "`// rayglow: scale=N` directive > config.DEFAULT_SCALE "
                         f"({config.DEFAULT_SCALE}). 1 = pixel-exact, 4 = smoother "
                         "but ~16x the GPU+readback cost. Adjustable live over the "
                         "control plane (rayglow-ctl scale N|auto)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="dry-run gamma (default 1.0 = LINEAR). Wall runs "
                         "ignore this: gamma there is config.PACK_GAMMA, "
                         "applied on the GPU by the resolve pass (or by the "
                         "packer's CIE LUT with --readback legacy)")
    ap.add_argument("--readback", default="auto",
                    choices=("auto", "dmabuf", "dmabuf-pipe", "glread",
                             "legacy"),
                    help="GPU->CPU frame path. auto (default): zero-copy "
                         "dma-heap readback of the GPU resolve pass, falling "
                         "back to glReadPixels where dmabuf isn't available "
                         "(e.g. desktop dry-runs). dmabuf-pipe: ping-pong two "
                         "buffers — reads frame N-1 while N renders (fastest, "
                         "+1 frame latency). glread: force the fallback. "
                         "legacy: the original full-size glReadPixels + CPU "
                         "postprocess path")
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
    ap.add_argument("--no-control", action="store_true",
                    help="don't open the TCP control plane (render/control.py). "
                         "By default the wall run listens on config.CONTROL_PORT "
                         "for push/load/next/prev/play/pause/loop/repeat/status "
                         "(see tools/rayglow_ctl.py)")
    ap.add_argument("--control-port", type=int, default=config.CONTROL_PORT,
                    help=f"control-plane TCP port (default {config.CONTROL_PORT})")
    args = ap.parse_args()

    # Geometry defaults to the full two-chain display (256x64).
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
    # shift/drop would skew the GIF). It belongs to the legacy readback path.
    dry = args.dry_run is not None
    use_pbo = (not dry) and args.pbo
    if args.pbo:
        args.readback = "legacy"
    if dry and args.readback == "dmabuf-pipe":
        # Pipelined reads return frame N-1 — that off-by-one would skew the
        # GIF, so dry-runs use the synchronous dmabuf reader instead.
        args.readback = "dmabuf"

    # What the resolve pass bakes in (see build_shader): wall runs get the
    # firmware gamma + physical mount flips on the GPU; dry-runs keep the
    # historical semantics (--gamma, no flips). Legacy applies --gamma in the
    # CPU readback LUT and leaves gamma/flips to the packer/run_wall.
    if args.readback == "legacy":
        args.render_gamma = args.gamma
        args.resolve_flip = (False, False)
    elif dry:
        args.render_gamma = args.gamma
        args.resolve_flip = (False, False)
    else:
        args.render_gamma = config.PACK_GAMMA
        args.resolve_flip = (config.FLIP_V, config.FLIP_H)
    toy, watchers, _ = build_shader(args, use_pbo, args.shader, fatal=True,
                                    scale_override=args.scale)
    assert toy is not None       # fatal=True exits on a compile error

    feed = AudioFeed(toy.audio_channels,
                     allow_listen=not args.no_listen and not dry)

    if dry:
        run_dry(toy, feed, args)
        return

    # Playback state for the live loop.  The folder playlist is always built (so
    # next/prev/loop work without --loop); --loop just seeds the auto-advance
    # interval.  index = -1 means the current shader isn't a member of its own
    # folder listing (shouldn't happen for a real launch, but stays safe).
    start = os.path.abspath(args.shader)
    playlist = build_playlist(args.shader)
    index = playlist.index(start) if start in playlist else -1
    player = PlayerState(playlist, index, loop_interval=args.loop,
                         display_name=os.path.basename(start), current_path=start,
                         scale_override=args.scale, scale=toy.scale)
    if args.loop is not None:
        if len(playlist) <= 1:
            print(f"--loop: only one shader in {os.path.dirname(start)} — "
                  "nothing to cycle (next/prev/load still work over control)")
        else:
            print(f"--loop: cycling {len(playlist)} shaders every "
                  f"{args.loop:g}s")

    # Control plane: a background TCP server hands commands to this render thread
    # via cmd_queue.  Bind failure (port in use) is non-fatal — run without it.
    cmd_queue = queue.Queue()
    server = None
    if not args.no_control:
        try:
            server = ControlServer(cmd_queue, port=args.control_port)
            print(f"control: listening on TCP {config.CONTROL_HOST}:"
                  f"{args.control_port}")
        except OSError as e:
            print(f"control: disabled — could not bind port "
                  f"{args.control_port}: {e}", file=sys.stderr)

    try:
        run_wall(toy, watchers, feed, args, player, cmd_queue, use_pbo=use_pbo)
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    main()
