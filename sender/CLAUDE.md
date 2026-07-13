# CLAUDE.md — sender/

Guidance for Claude Code in the RayGLow **sender** (desktop half). Repo-wide
orientation is in the top-level `../CLAUDE.md`; read this directory's `README.md` first
for the protocol and feature detail.

## What this is

The desktop feature daemon (`sender.py` + `beat.py`): captures the PipeWire monitor of
the default sink, runs the v3 analysis (8 log bands with flywheel envelopes/thetas on
MilkDrop-style AutoGain, spectrum/chroma/descriptors/stereo, a predictive beat tracker,
key detection — plus the legacy MilkDrop bands, still exact), and unicasts ~3 KB v3
feature packets over UDP at ~60 Hz to the Pi (192.168.0.50:5005). It's a standalone uv
project — it shares *no code* with the `rayglow` package, only the packet contract
mirrored in `rayglow/feed/receiver.py` (and `rayglow/fake_sender.py`; `tools/
feed_check.py` asserts the three stay identical). The renderer that consumes these
packets is `rayglow.render` (GLSL on the Pi). `docs/design-history/project-milk-pi.md`
is the historical record (MilkDrop reverse-engineering, v0 spec) — background,
superseded where it disagrees with the READMEs.

## Commands

```fish
uv run sender.py                  # capture default sink's monitor, send to the Pi
uv run sender.py --list-sources   # enumerate pulse sources
uv run sender.py --debug          # adds legacy bands + raw energies to the 1 Hz status line
uv run sender.py --host X --port N --fps N --source NAME
uv run beat.py                    # offline beat-tracker harness (synthetic click tracks)
```

uv project (Python ≥3.13, numpy + sounddevice). No tests, no linter, but two mechanical
checks: `uv run beat.py` (tempo/phase lock on synthetic clicks) and
`uv run --with numpy ../tools/feed_check.py` (packet contract roundtrip; its `--live`
mode pretty-prints real packets). Validation beyond that is empirical: sine tones at the
band centers (35/85/173/354/707/1581/3873/9798 Hz — each should dominate exactly its
band in `feed_check.py --live`) and watching the panel.

## Architecture and invariants

`sender.py` is a faithful port of MilkDrop3's analysis (`vis_milk2/fft.cpp` +
`plugin.cpp:8736/8750`; cross-reference against a local checkout of the MilkDrop3
source — https://github.com/milkdrop2077/MilkDrop3). Rules that look like bugs but
aren't:

- **Replicate the code, not the comments** (legacy path). MilkDrop's fft.cpp comments
  recommend octave bands; the actual code uses three equal *linear* thirds of the bottom
  half-spectrum (bins [0:85], [85:170], [170:256]). Equalize is ON (a `-1` arg lands on
  a bool). Source line references are cited inline throughout — keep them accurate when
  editing. The legacy bands are deprecated-but-stable: they still ship in every packet
  (and in the milk texture's legacy texel block) so pre-v3 shaders stay exact.
- **The v3 bands deliberately have NO equalize** — each has its own AutoGain, which is
  the leveling. Don't "fix" them to match the legacy path's equalize table. Their bin
  slices derive from `BAND_EDGES_V3` via searchsorted (never hardcode bins), and the
  flywheel ballistics live in the one `ENV_TIERS` table — those two tables plus
  `BAND_IMM_CLAMP` are the intended tune-on-the-wall knobs.
- **`beat.py` is clean-room, not MilkDrop.** It implements the Stark/Davies/Plumbley
  DAFx-09 tracker from the papers; BTrack (the reference implementation) is GPL — don't
  port its code in. The tracker's beat_phase is PREDICTIVE (ramps 0→1, hits 1.0 ON the
  beat); keep that contract, shaders anticipate with it.
- **The packet is a cross-machine contract.** `PACKET_FMT` (2996 bytes, v3) must match
  `rayglow/feed/receiver.py`, which asserts the sizes at import and dispatches on
  `(version, exact byte length)` — accepting v0 (556 B), v1 (564 B = v0 + `(sub,
  sub_att)`, `sub = bass` for v0), v2 (4236 B, the 512-bin-spectrum era) and v3 (the
  band/flywheel feed). Older senders get zeros/defaults for fields they don't carry.
  Any layout change must be made in lockstep with the receiver AND
  `rayglow/fake_sender.py`, and bump `VERSION` — run `tools/feed_check.py` after.
  Downstream of the receiver, shaders consume the feed via the `milk`/`spectrum`/`audio`
  iChannel textures (`rayglow/render/textures.py`); the texel map there is calibrated to
  AutoGain's "1.0 = typical" semantics. (Note: "milk" survives as the *iChannel spec
  name* and the packet magic — it is not the package name. Don't rename those.)
- **The `sub` band is intentionally non-MilkDrop.** MilkDrop's "bass" is 0–4 kHz with a
  log-equalize that suppresses the lowest bins ~90×, so subwoofer content is invisible in
  it. `analyze_sub()` deliberately uses a longer window (2048) and *no* equalize. Don't
  "fix" the inconsistency between `analyze()` and `analyze_sub()`.
- **`import sounddevice` is deferred into `main()`** because `PULSE_SOURCE` must be set
  in the environment before PortAudio initializes — that env var is how the ALSA "pulse"
  device is pointed at the monitor source. Don't hoist it to module level.
- **AutoGain semantics** (ported from plugin.cpp:8750): bands are normalized by their own
  running average so values hover ~1.0 regardless of genre/volume — shaders on the Pi
  depend on this. Decay rates are FPS-corrected via `adjust_rate_to_fps` (30 fps
  reference).
- The send loop is wall-clock paced (`next_tick += 1/fps`), latest-wins on the receiver
  side; never block sending on capture state.

## Microphone input

The microphone path exists as two sibling sub-projects rather than a `sender.py` mode:
`esp32-mic/` (ESP32 + I2S mic firmware computing the same features on-chip, speaking
an earlier packet version — the receiver accepts every version, so it needs no v3
update) and `espnow-dongle/` (ESP-NOW → UDP bridge + `pi_bridge.py`
+ systemd unit on the Pi). Keep `sender.py`'s capture path factored anyway — a local
mic source may still slot in someday.
