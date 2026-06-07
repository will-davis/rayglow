# docs/design-history/

Superseded design documents, kept for provenance. These reflect what was true when they
were written — where they disagree with the top-level README or the package docs, the
current docs win.

- **`project-milk-pi.md`** — the original handoff brief from when RayGLow was a
  from-scratch MilkDrop port. Still the best reference for the MilkDrop
  reverse-engineering and the "why from scratch" rationale; its packet spec is the v0
  ancestor of today's v1.
- **`BUILD-HISTORY.md`** — a knowledge-export brain-dump from the Pi-side build agent:
  decision log, dead ends, hardware/environment quirks (the panel-1 artifact hunt, the
  PWM solder mod, V3D/EGL behavior), calibration constants, load-bearing oddities, and a
  rough timeline. Captures what lived only in a long agent context, so it isn't lost when
  that context is. Written against the pre-restructure layout — see its header banner for
  the path remap.
