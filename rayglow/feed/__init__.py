"""rayglow.feed — the audio-feature feed: protocol, state, and rig config.

The neutral contract shared by every renderer (live or legacy):
  config    — panel geometry, UDP host/port, gamma, matrix options.  Single
              source of truth; the target of the future yaml-config extraction.
  receiver  — nonblocking latest-wins UDP receiver (packet v0 + v1).
  features  — FeatureState: latest packet values + synth fallback.

The packet protocol itself is documented in receiver.py; the desktop end that
produces these packets is sender/sender.py.
"""
