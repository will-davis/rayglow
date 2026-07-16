# Renderer control plane — the dev loop + media controls (shipped 2026-07-14)

The design record for the renderer control plane: why it exists, the decisions
taken, and what it supersedes. Current truth lives in `rayglow/render/control.py`
(server + protocol + `PlayerState`), `rayglow/render/__main__.py` (the render-thread
command drain: `switch_to` / `_advance` / `drain_commands` / `_run_command`),
`tools/rayglow_ctl.py` (the client), and `tools/control_check.py` (the wire test).

## The problem

Two overlapping pains in the shader-dev workflow:

1. **Save→wall latency was 5–20s.** The renderer already re-`stat`s the shader
   every frame (`reload.GlslWatcher`), so render-side detection is ~one frame —
   the latency was entirely **mutagen** propagating the file desktop→Pi. Tuning a
   color constant (nudge, wait, look, repeat) was miserable.
2. **No structured control** over what's playing beyond launch args and editing
   the file. Will wanted media controls (play/pause/next/prev/loop/repeat), a way
   to *force* a reload, and ideally a direct push.

This is the escalation path the old ROADMAP §2 named ("a tiny UDP/OSC control
socket … the natural HA integration point later"). §2's baseline — copy a file
over `now-playing.glsl` and let hot-reload catch it — was rejected because it
still rides mutagen and so wouldn't fix the latency.

Mental model: **mpd/mpv with an IPC socket.** Long-running renderer daemon +
control socket + a `mpc`-style client. nvim's save hook, an ssh one-liner, and any
future web/HA UI are all just clients of one protocol.

## Decisions (Will)

- **LAN TCP**, not UDP or localhost-only. Mirrors the feed's reach (the Pi listens
  on the network, desktop pushes straight to it). TCP because control is low-rate
  and reliability-sensitive — a dropped "next" or a truncated shader push is a
  bug, the opposite of the feed's lossy latest-wins stream. Lock down via firewall
  or `CONTROL_HOST=127.0.0.1` + ssh tunnel if the LAN is ever untrusted.
- **Pause = freeze the shader clock** (iTime/iFrame). The audio feed keeps running
  (decoupled clock), so a resume picks up live. A paused wall also stops
  auto-advancing. (loop = auto-advance the folder; repeat = hold current; so pause
  is free to mean "freeze the visual".)
- **Push = restart from t=0.** Every content change (push/load/next/prev/reload) is
  a uniform *full rebuild* (`build_shader`), resetting iTime/iFrame and clearing
  multipass buffers. This made all switches one code path and — as a free
  bonus — fixed §2's noted multipass gotcha (buffer siblings are rediscovered on
  every build, so a push can add/remove passes; the in-place mtime reload can't).

## The design

- **Transport / protocol** (`control.py`): a background `ControlServer` (daemon
  accept loop, per-connection handler threads) bound to `config.CONTROL_PORT`
  (5006). Framing is a 4-byte big-endian length prefix + UTF-8 JSON
  (`send_msg`/`recv_msg`) — length-prefixed so partial TCP reads reassemble, JSON
  because it's the control plane (not the frame plane): `socat`-debuggable and
  extensible (a future `brightness` cmd, per-shader settings). GLSL is text, so a
  shader rides in a JSON string field.
- **GL thread-affinity is the load-bearing constraint.** The GL context belongs to
  the render thread, so socket threads must never touch it. Every request becomes a
  `Command` on a `queue.Queue`; the render thread drains and executes it (the sole
  reader/writer of `PlayerState`, so no locks), fills the reply, and signals the
  waiting handler thread — which returns the reply (incl. compile errors) to the
  client. Latency ≤ one frame (~8ms). A bad command can never kill the loop.
- **Compile errors flow back to the client.** `build_shader(fatal=False)` now
  returns its error message; a failed `push`/`load` keeps the last good shader on
  the wall and replies `{"ok": false, "error": ...}`. The nvim hook surfaces that
  in the editor — GLSL errors no longer vanish into the Pi's stderr.
- **Live slot** (`config.LIVE_DIR`, `~/.cache/rayglow/live/`): `push` writes the
  bundle there (image + `.bufX.glsl` siblings + base64 image assets) and rebuilds
  from it — kept out of the mutagen tree so a push never fights the sync. This is
  the "tmp file that holds what's running" from Will's brief.
- **Playback state** (`PlayerState`): the folder playlist (always built, so
  next/prev/loop work without `--loop`; `--loop N` just seeds the interval), index,
  loop/repeat/pause, and the pausable shader clock. `switch_to` does the full
  rebuild + clock reset; `_advance` walks the playlist to the next compilable
  shader (shared by `--loop` auto-advance and next/prev).
- **The mtime `GlslWatcher` stays** as a cheap fallback (direct-edit-on-Pi,
  mutagen-eventual). Push is the fast path.

## Protocol (v1)

`push` (name/passes/assets), `load` (path), `next`, `prev`, `play`, `pause`,
`loop` (seconds|off), `repeat` (on|off/toggle), `reload`, `status`. Reply is
`{"ok": bool, ...}`; switch/status replies carry a player snapshot.

## Verification

- Wire contract, no hardware: `uv run tools/control_check.py` (framing round-trip,
  oversized-frame rejection, snapshot fields, the client's bundler).
- Command dispatch + a real GL switch/clock-reset were confirmed on desktop EGL.
- On the panel: `rayglow-ctl push/next/pause/loop/status` against a live renderer.

## Known limits / future

- A `../` asset path that climbs out of the shader folder won't resolve in the
  flat live slot — use `load` for those (rare). A content-hash cache could avoid
  re-sending a heavy texture atlas on every save.
- Brightness (ROADMAP §1) and per-shader settings (§2) are the natural next
  commands on this channel; HA integration is any client speaking the protocol.

## Follow-on: runtime supersample scale (2026-07-16)

Extended the plane with a `scale` command and shipped the ROADMAP §2 settings
namespace as its vehicle. `textures.parse_settings` reads `// rayglow: key=val`
comments (parallel to `// iChannelN:`); `build_shader` resolves the effective
supersample factor as **override (CLI `--scale` / runtime `scale` command) >
`// rayglow: scale=N` directive > `config.DEFAULT_SCALE`**, reading the image
source up front so the FBOs are sized before the toy is constructed. Because a
scale change is an FBO reallocation, the `scale` command just sets
`player.scale_override` and rebuilds the current shader through `switch_to` — no
new rendering path, and the override is sticky across switches until `scale
auto`. `PlayerState` gained `scale`/`scale_override` (in `status`); the nvim menu
gained `<leader>mx{1..4,a}`. This is the template for the remaining §2 settings
(gamma/fps/brightness): a `parse_settings` key + a matching command.
