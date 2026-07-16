#!/usr/bin/env python3
"""Prove the control-plane wire contract: rayglow_ctl (client) ⇄ control.py
(server framing) ⇄ the render thread's command queue.

tools/feed_check.py locks the audio→render packet and tools/verify.py locks the
render→firmware frame; this locks the desktop→renderer control channel — the
third cross-process contract.  It runs with no GPU and no panel: a mock "render
thread" stands in for run_wall's per-frame command drain (the real command
dispatch, _run_command, lives in the GL-importing __main__ and is exercised
empirically on the wall).

What it checks:
  1. framing round-trips — every command the client builds arrives intact and
     its reply comes back (one command per connection, and several multiplexed
     on one persistent connection, in order)
  2. an oversized length prefix is rejected without hanging the server
  3. PlayerState.snapshot() exposes the fields `status` promises
  4. the client's push bundler (build_push) captures buffer siblings and does
     NOT mistake procedural channels (milk/audio/noise) for image assets

Run: uv run tools/control_check.py
"""
import os
import queue
import socket
import struct
import sys
import tempfile
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)                     # import rayglow.*
sys.path.insert(0, os.path.join(_ROOT, "tools"))   # import rayglow_ctl

from rayglow.render.control import (ControlServer, PlayerState,  # noqa: E402
                                    recv_msg, send_msg)
import rayglow_ctl                             # noqa: E402


def _mock_render(cmd_queue, stop, recorded):
    """Stand-in for run_wall's drain_commands: pull each Command, record it, and
    reply with a canned snapshot — the same handshake the real render thread
    performs (fill reply, set done)."""
    while not stop.is_set():
        try:
            cmd = cmd_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        recorded.append(cmd.msg)
        cmd.reply = {"ok": True, "echo": cmd.msg.get("cmd")}
        cmd.done.set()


def _send_one(port, msg):
    with socket.create_connection(("127.0.0.1", port), 2) as s:
        send_msg(s, msg)
        return recv_msg(s)


def check_framing():
    q = queue.Queue()
    server = ControlServer(q, host="127.0.0.1", port=0)   # 0 => ephemeral port
    port = server.sock.getsockname()[1]
    recorded, stop = [], threading.Event()
    t = threading.Thread(target=_mock_render, args=(q, stop, recorded),
                         daemon=True)
    t.start()
    try:
        # 1a: one command per connection, across every client verb.
        cmds = [
            {"cmd": "status"}, {"cmd": "play"}, {"cmd": "pause"},
            {"cmd": "next"}, {"cmd": "prev"}, {"cmd": "reload"},
            {"cmd": "loop", "seconds": 30.0}, {"cmd": "loop", "off": True},
            {"cmd": "repeat"}, {"cmd": "repeat", "on": False},
            {"cmd": "load", "path": "/x/y.glsl"},
            {"cmd": "scale", "value": 3}, {"cmd": "scale", "value": "auto"},
        ]
        for c in cmds:
            r = _send_one(port, c)
            assert r == {"ok": True, "echo": c["cmd"]}, (c, r)

        # 1b: several commands multiplexed on ONE persistent connection, in order.
        with socket.create_connection(("127.0.0.1", port), 2) as s:
            for name in ("pause", "play", "next"):
                send_msg(s, {"cmd": name})
                r = recv_msg(s)
                assert r and r["echo"] == name, name

        # 2: an oversized length prefix is rejected, not fatal to the server.
        with socket.create_connection(("127.0.0.1", port), 2) as s:
            s.sendall(struct.pack(">I", 999_999_999) + b"{")
            reply = recv_msg(s)
            assert reply and reply["ok"] is False and "error" in reply, reply
        # server still serves after the bad frame:
        r = _send_one(port, {"cmd": "status"})
        assert r and r["ok"] is True
    finally:
        stop.set()
        server.close()
    print(f"  framing: {len(recorded)} commands round-tripped, "
          "oversized frame rejected, server survived")


def check_snapshot():
    p = PlayerState(playlist=["/a.glsl", "/b.glsl"], index=0,
                    loop_interval=None, display_name="a.glsl",
                    current_path="/a.glsl", scale_override=None, scale=2)
    snap = p.snapshot()
    for key in ("shader", "path", "index", "playlist_len", "paused", "loop",
                "repeat", "scale", "scale_override", "fps"):
        assert key in snap, f"snapshot missing {key}"
    assert snap["playlist_len"] == 2 and snap["shader"] == "a.glsl"
    assert snap["scale"] == 2 and snap["scale_override"] is None
    print("  snapshot: all status fields present (incl. scale)")


def check_bundle():
    with tempfile.TemporaryDirectory() as d:
        img = os.path.join(d, "demo.glsl")
        with open(img, "w") as f:
            f.write("// iChannel0: milk\n// iChannel1: bufA\n"
                    "// iChannel2: noise:7\nvoid mainImage(){}\n")
        with open(os.path.join(d, "demo.bufA.glsl"), "w") as f:
            f.write("// iChannel0: self\nvoid mainImage(){}\n")
        b = rayglow_ctl.build_push(img)
    assert b["name"] == "demo.glsl", b["name"]
    assert set(b["passes"]) == {"image", "bufA"}, set(b["passes"])
    # milk/audio/noise/self/bufA are procedural or pass refs — NOT image assets.
    assert b["assets"] == {}, b["assets"]
    print("  bundle: buffer sibling captured, procedural channels not "
          "mistaken for assets")


def check_bundle_image_asset():
    with tempfile.TemporaryDirectory() as d:
        os.mkdir(os.path.join(d, "fonts"))
        png = os.path.join(d, "fonts", "atlas.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)     # dummy bytes
        img = os.path.join(d, "textcard.glsl")
        with open(img, "w") as f:
            f.write("// iChannel0: fonts/atlas.png\nvoid mainImage(){}\n")
        b = rayglow_ctl.build_push(img)
    assert list(b["assets"]) == ["fonts/atlas.png"], b["assets"]
    print("  bundle: referenced image asset base64-bundled under its "
          "directive path")


def main():
    print("control-plane contract check")
    check_framing()
    check_snapshot()
    check_bundle()
    check_bundle_image_asset()
    print("OK — control-plane wire contract holds")


if __name__ == "__main__":
    main()
