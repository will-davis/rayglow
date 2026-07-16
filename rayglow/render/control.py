"""Renderer control plane — the live-dev push channel + media controls.

A long-running renderer (``run_wall``) is an mpd/mpv-style daemon; this module is
its IPC socket. A tiny CLI (``tools/rayglow_ctl.py``), an nvim save-hook, or a
future web/HA UI are all just clients speaking one protocol.

Why this exists: the mtime hot-reload (`reload.GlslWatcher`) already re-stats the
shader every frame, so render-side detection is ~one frame. The 5-20s save->wall
latency is entirely mutagen propagating the file desktop->Pi. A ``push`` hands the
source straight to the running renderer, bypassing mutagen for the dev loop
(mutagen stays the durable background library sync).

Transport: **TCP**, not the feed's UDP. Control is low-rate and reliability-
sensitive — a dropped "next" or a truncated shader push is a bug, the opposite of
the feed's lossy latest-wins stream. Framing is a 4-byte big-endian length prefix
+ UTF-8 JSON (`send_msg`/`recv_msg`): length-prefixed so partial TCP reads
reassemble, JSON because this is the control plane (not the frame plane) — it's
`socat`-debuggable and extends cleanly (a future brightness cmd, per-shader
settings). GLSL is text, so a shader rides in a JSON string field.

Threading & GL affinity: the GL context is thread-affine, so socket threads must
NEVER touch GL. Every request becomes a `Command` on a `queue.Queue`; the render
thread drains and executes it (the sole reader/writer of `PlayerState`, so no
locks), fills the reply, and signals the waiting handler thread — which returns the
reply (incl. compile errors) to the client. Latency is <= one frame (~8ms).

Protocol (v1) — request ``{"cmd": ..., ...}`` -> reply ``{"ok": bool, ...}``:

    push    name, passes{image,bufA..D}, assets?  write live slot, full rebuild
    load    path                                  full rebuild from a Pi path
    next / prev                                   step the folder playlist
    play / pause                                  resume / freeze the shader clock
    loop    seconds | off                         set/clear auto-advance interval
    repeat  on | off (toggles if absent)          hold current (suppress advance)
    reload                                         re-read current from disk
    status                                         player snapshot
"""
import json
import socket
import struct
import threading

from ..feed import config

_LEN = struct.Struct(">I")      # 4-byte big-endian length prefix
MAX_MSG = 8 * 1024 * 1024       # 8 MiB ceiling — a shader bundle is tiny; a big
                                # texture asset is the only thing near this


def send_msg(sock, obj):
    """Frame `obj` as length-prefixed UTF-8 JSON and write it to `sock`."""
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(_LEN.pack(len(body)) + body)


def _recv_exact(sock, n):
    """Read exactly n bytes or return None on clean EOF (peer closed)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def recv_msg(sock):
    """Read one framed message -> dict, or None on clean EOF.

    Raises ValueError on a length that exceeds MAX_MSG (a malformed or hostile
    prefix) so the handler can drop the connection instead of trying to allocate
    it."""
    head = _recv_exact(sock, _LEN.size)
    if head is None:
        return None
    (length,) = _LEN.unpack(head)
    if length > MAX_MSG:
        raise ValueError(f"message length {length} exceeds {MAX_MSG}")
    body = _recv_exact(sock, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


class Command:
    """One request in flight: the socket thread enqueues it and blocks on `done`;
    the render thread fills `reply` and sets `done`."""

    __slots__ = ("msg", "reply", "done")

    def __init__(self, msg):
        self.msg = msg
        self.reply = None
        self.done = threading.Event()


class PlayerState:
    """Playback state for the live loop. Mutated ONLY by the render thread (the
    command drain), so it needs no lock. The socket threads never read it — a
    `status` request is a `Command` the render thread answers via `snapshot()`."""

    def __init__(self, playlist, index, loop_interval, display_name, current_path,
                 scale_override=None, scale=None):
        self.playlist = playlist        # sorted abs paths in `folder` (may be [])
        self.index = index              # position in playlist (or -1 if off-list)
        self.loop_interval = loop_interval   # seconds, or None = auto-advance off
        self.repeat = False             # hold current: suppress auto-advance
        self.paused = False             # freeze the shader clock (not the feed)
        self.display_name = display_name     # what status/prints show
        self.current_path = current_path     # the .glsl actually being rendered
        # Scale (supersample factor). scale_override is the explicit request (CLI
        # --scale seed or a runtime `scale` command); None = auto (defer to a
        # `// rayglow: scale=` directive, else config.DEFAULT_SCALE). Sticky
        # across switches until set back to auto. `scale` is the effective value
        # the current toy is actually rendering at (set by switch_to).
        self.scale_override = scale_override
        self.scale = scale
        # Pausable shader clock — accumulates real dt only while not paused, so
        # iTime/iFrame freeze on pause and resume without a jump. Reset to 0 on
        # every switch/push/reload (full rebuild => t=0, per the design).
        self.shader_time = 0.0
        self.shader_frame = 0
        self.fps = 0.0                  # render loop stamps this for status

    @property
    def folder(self):
        import os
        return os.path.dirname(self.current_path)

    def snapshot(self):
        return {
            "shader": self.display_name,
            "path": self.current_path,
            "index": self.index,
            "playlist_len": len(self.playlist),
            "paused": self.paused,
            "loop": self.loop_interval,
            "repeat": self.repeat,
            "scale": self.scale,
            "scale_override": self.scale_override,   # null => auto (directive/default)
            "fps": round(self.fps, 1),
        }


class ControlServer:
    """Background TCP server. Accepts connections on their own daemon threads,
    turns each framed request into a `Command` on `self.commands`, and waits for
    the render thread to fill the reply. Owns no GL and no PlayerState."""

    def __init__(self, commands, host=None, port=None, reply_timeout=5.0):
        self.commands = commands        # queue.Queue shared with the render loop
        self.host = config.CONTROL_HOST if host is None else host
        self.port = config.CONTROL_PORT if port is None else port
        self.reply_timeout = reply_timeout
        self._stop = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(8)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                break                   # socket closed by close()
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            t.start()

    def _serve(self, conn):
        """Handle one connection: for each framed request, enqueue a Command,
        wait for the render thread's reply, and send it back. Loops until the
        client closes (so a client may reuse the connection)."""
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while not self._stop:
                try:
                    msg = recv_msg(conn)
                except (ValueError, json.JSONDecodeError) as e:
                    send_msg(conn, {"ok": False, "error": f"bad message: {e}"})
                    return
                if msg is None:
                    return              # clean EOF
                cmd = Command(msg)
                self.commands.put(cmd)
                if cmd.done.wait(self.reply_timeout):
                    send_msg(conn, cmd.reply)
                else:
                    send_msg(conn, {"ok": False,
                                    "error": "renderer did not respond "
                                             "(stalled or shutting down)"})
        except OSError:
            pass                        # peer vanished mid-exchange
        finally:
            conn.close()

    def close(self):
        self._stop = True
        try:
            self.sock.close()           # unblocks accept()
        except OSError:
            pass
