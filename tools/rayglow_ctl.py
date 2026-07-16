#!/usr/bin/env python3
"""rayglow-ctl — drive a running RayGLow renderer over its TCP control plane.

The renderer (`python -m rayglow.render …`) listens on config.CONTROL_PORT
(5006).  This is the mpc-to-its-mpd: a thin client that frames one JSON command,
sends it, and prints the reply.  Stdlib only (socket/json/struct) so it runs on
the desktop without the Pi package installed — the framing + directive regex
below intentionally mirror rayglow/render/control.py and rayglow/render/
textures.py (kept in sync by hand; both are tiny and stable).

The headline use is the mutagen-free dev loop: `push` reads a local .glsl (plus
its buffer siblings and any referenced image assets), ships the source straight
to the running renderer, and the wall updates in <100ms — no waiting on the
file sync.  Compile errors come back in the reply (surface them in your editor).

Target host: --host, else $RAYGLOW_HOST (shared with the sender — same Pi), else
127.0.0.1 (on-Pi default).  On the desktop, `set -x RAYGLOW_HOST <pi>` once.
Port: --port, else $RAYGLOW_CONTROL_PORT, else 5006 (NOT $RAYGLOW_PORT — that's
the sender's UDP feed port, a different socket).

    rayglow-ctl push presets/milkfeed.glsl   # edit + push (dev loop)
    rayglow-ctl load ~/presets/will/foo.glsl # switch to a shader already on the Pi
    rayglow-ctl next / prev / play / pause / reload
    rayglow-ctl loop 30      rayglow-ctl loop off
    rayglow-ctl repeat       rayglow-ctl repeat off
    rayglow-ctl scale 3      rayglow-ctl scale auto   # supersample (rebuilds)
    rayglow-ctl status

Run: uv run tools/rayglow_ctl.py <cmd> …   (or plain python3 — no deps)
"""
import argparse
import base64
import json
import os
import re
import socket
import struct
import sys

DEFAULT_PORT = 5006
_LEN = struct.Struct(">I")   # must match control.py
# Mirror of textures._DIRECTIVE — extract `// iChannelN: spec`.
_DIRECTIVE = re.compile(r"^\s*//\s*iChannel([0-3])\s*[:=]\s*(.+?)\s*$",
                        re.MULTILINE)
# Specs that need no companion file (generated in-process or another pass).
_PROCEDURAL = {"milk", "audio", "spectrum", "self"}


def send_msg(sock, obj):
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(_LEN.pack(len(body)) + body)


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def recv_msg(sock):
    head = _recv_exact(sock, _LEN.size)
    if head is None:
        return None
    (length,) = _LEN.unpack(head)
    body = _recv_exact(sock, length)
    return None if body is None else json.loads(body.decode("utf-8"))


def _is_image_spec(spec):
    """A directive spec that names an on-disk image (needs bundling), vs a
    procedural/buffer channel that doesn't."""
    head = spec.split(":", 1)[0]
    return not (head in _PROCEDURAL or head == "noise"
                or re.fullmatch(r"buf[A-D]", head))


def build_push(path):
    """Read a shader file into a push bundle: {name, passes{image,bufA..D},
    assets{directive-path: base64}}.  Buffer siblings (foo.bufX.glsl) and any
    image files named by `// iChannelN:` directives ride along, so a multipass
    or textured shader stays self-contained over the wire.  (Known limit: a
    `../` asset path that climbs out of the shader's folder won't resolve in the
    renderer's flat live slot — use `load` for those.)"""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        sys.exit(f"push: no such file: {path}")
    base = re.sub(r"\.glsl$", "", path)
    passes = {"image": _read(path)}
    for x in "ABCD":
        sib = f"{base}.buf{x}.glsl"
        if os.path.exists(sib):
            passes[f"buf{x}"] = _read(sib)
    shader_dir = os.path.dirname(path)
    assets = {}
    for src in passes.values():
        for _idx, spec in _DIRECTIVE.findall(src):
            spec = spec.strip()
            if not _is_image_spec(spec) or spec in assets:
                continue
            asset = spec if os.path.isabs(spec) else os.path.join(shader_dir, spec)
            if os.path.exists(asset):
                with open(asset, "rb") as f:
                    assets[spec] = base64.b64encode(f.read()).decode("ascii")
            else:
                print(f"push: warning — asset {spec!r} not found locally; the "
                      "renderer will fall back to whatever it has synced",
                      file=sys.stderr)
    return {"name": os.path.basename(path), "passes": passes, "assets": assets}


def _read(p):
    with open(p) as f:
        return f.read()


def build_message(args):
    """Turn parsed CLI args into the JSON command dict."""
    c = args.cmd
    if c == "push":
        return {"cmd": "push", **build_push(args.file)}
    if c == "load":
        return {"cmd": "load", "path": args.path}
    if c == "loop":
        if args.arg is None or args.arg.lower() in ("off", "none", "0"):
            return {"cmd": "loop", "off": True}
        try:
            return {"cmd": "loop", "seconds": float(args.arg)}
        except ValueError:
            sys.exit(f"loop: expected SECONDS or 'off', got {args.arg!r}")
    if c == "repeat":
        if args.arg is None:
            return {"cmd": "repeat"}                 # toggle
        return {"cmd": "repeat", "on": args.arg.lower() in ("on", "1", "true")}
    if c == "scale":
        if args.arg.lower() in ("auto", "none", "0"):
            return {"cmd": "scale", "value": "auto"}
        try:
            return {"cmd": "scale", "value": int(args.arg)}
        except ValueError:
            sys.exit(f"scale: expected 1..8 or 'auto', got {args.arg!r}")
    return {"cmd": c}                                # next/prev/play/pause/reload/status


def main():
    ap = argparse.ArgumentParser(prog="rayglow-ctl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("RAYGLOW_HOST", "127.0.0.1"),
                    help="renderer host (default $RAYGLOW_HOST or 127.0.0.1)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("RAYGLOW_CONTROL_PORT",
                                               DEFAULT_PORT)),
                    help=f"control port (default $RAYGLOW_CONTROL_PORT or "
                         f"{DEFAULT_PORT}; distinct from the UDP feed's "
                         f"$RAYGLOW_PORT)")
    ap.add_argument("--timeout", type=float, default=6.0,
                    help="socket timeout seconds (default 6)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("push", help="push a local .glsl to the wall now (dev loop)")
    sp.add_argument("file")
    sp = sub.add_parser("load", help="switch to a shader path already on the Pi")
    sp.add_argument("path")
    sub.add_parser("next", help="next shader in the folder")
    sub.add_parser("prev", help="previous shader in the folder")
    sub.add_parser("play", help="resume the shader clock")
    sub.add_parser("pause", help="freeze the shader clock")
    sub.add_parser("reload", help="re-read the current shader from disk")
    sub.add_parser("status", help="print the player state")
    sp = sub.add_parser("loop", help="auto-advance every SECONDS, or 'off'")
    sp.add_argument("arg", nargs="?")
    sp = sub.add_parser("repeat", help="hold current shader (toggle, or on/off)")
    sp.add_argument("arg", nargs="?")
    sp = sub.add_parser("scale", help="supersample scale: 1..8, or 'auto'")
    sp.add_argument("arg")
    args = ap.parse_args()

    msg = build_message(args)
    try:
        with socket.create_connection((args.host, args.port), args.timeout) as s:
            s.settimeout(args.timeout)
            send_msg(s, msg)
            reply = recv_msg(s)
    except OSError as e:
        sys.exit(f"rayglow-ctl: cannot reach {args.host}:{args.port} — {e}")

    if reply is None:
        sys.exit("rayglow-ctl: no reply (connection closed)")
    if reply.get("ok"):
        # status/switch replies carry a snapshot; print the useful fields.
        fields = {k: v for k, v in reply.items() if k != "ok"}
        print(json.dumps(fields) if fields else "ok")
        sys.exit(0)
    sys.exit(f"rayglow-ctl: {reply.get('error', 'command failed')}")


if __name__ == "__main__":
    main()
