# Local setup (template)

RayGLow ships with **generic placeholder** addresses/paths so the repo is clean to
publish. Your real rig values live in `LOCAL-SETUP.md` (this file's gitignored twin —
copy this file to it and fill in the blanks). Nothing in the tracked code reads
`LOCAL-SETUP.md`; it's a human record + a place to keep the two env vars the sender
honours.

## Runtime overrides (the only values code actually reads)

The sender resolves the Pi's address as `--host` → `$RAYGLOW_HOST` → built-in
placeholder. Set the env vars once (e.g. in `local.env`, also gitignored, or your
shell profile):

```fish
set -x RAYGLOW_HOST 192.168.0.50      # your Pi's IP
set -x RAYGLOW_PORT 5005              # UDP feature port (default 5005)
```

Everything else below is just edited directly in the files noted, or recorded here so
you remember what the placeholders stand for on your network.

## Value map (placeholder → your value)

| Thing | Placeholder in the repo | Your value | Where it appears |
|---|---|---|---|
| Pi IP (feature receiver / SPI host) | `192.168.0.50` | `__________` | `$RAYGLOW_HOST`, sender docs |
| Desktop IP (sender host) | `192.168.0.10` | `__________` | design-history diagrams only |
| Pi venv path | `~/venv` | `__________` | README/CLAUDE deploy, run examples |
| Pi clone path | `~/rayglow` | `__________` | deploy `git clone` target |
| Pi filesystem mount on desktop | `~/pi-mount` | `__________` | convenience sshfs/NFS mount |
| UDP feature port | `5005` | `__________` | `config.UDP_PORT`, `$RAYGLOW_PORT` |

## Rig-specific hardware notes (edit in `rayglow/feed/config.py`)

- `SPI_FLIP_H` / `SPI_FLIP_V` — panel-wall orientation. Defaults assume a 180°-mounted
  wall; verify yours with `python -m rayglow.spi_test --fliph --flipv` and set to match.
- Panel geometry (`ROWS`/`COLS`/`CHAIN`/`SPI_PARALLEL`) — change if your tile count differs.
- If the feed crosses VLANs/subnets, add a firewall rule for the UDP port.
