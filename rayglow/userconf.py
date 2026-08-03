"""Per-machine CLI defaults from a TOML config file (rayglow.example.toml).

Why this exists: the same repo tree is mutagen-synced to every machine, so
"what flags THIS machine should default to" cannot live in the tree — the
render host wants `--output net --net-host <pi> --egl device`, the Pi wants
`--output kms` and a framesink window, the desktop wants plain dry-runs.  A
config file OUTSIDE the synced tree gives each machine its own defaults and
turns the production launches into bare `python -m rayglow.render <shader>` /
`python -m rayglow.framesink`.

Precedence (least to most specific):  built-in default  <  config file  <
CLI flag.  Implemented via argparse set_defaults(), so an explicit flag
always wins and --help still shows the built-ins.

Search order (first hit wins):
  1. $RAYGLOW_CONFIG            — explicit override, any path
  2. ./rayglow.toml             — gitignored; fine for single-machine clones,
                                  but on a mutagen-synced rig it would ship to
                                  every machine and defeat the purpose — use 3.
  3. ~/.config/rayglow/config.toml   — the per-machine home (recommended)

Keys are the long flag names ('-' and '_' interchangeable); values are plain
TOML types — argparse's type= converters don't run on defaults, and TOML
already delivers real ints/floats/bools/strings.  Unknown keys and
out-of-choices values warn on stderr and are skipped, never fatal: a typo in
a config file must not take down a wall run.
"""
import os
import sys
import tomllib


def _find():
    explicit = os.environ.get("RAYGLOW_CONFIG")
    if explicit:
        path = os.path.expanduser(explicit)
        if not os.path.exists(path):
            print(f"config: $RAYGLOW_CONFIG={explicit} does not exist",
                  file=sys.stderr)
            return None
        return path
    for cand in ("rayglow.toml",
                 os.path.expanduser("~/.config/rayglow/config.toml")):
        if os.path.exists(cand):
            return cand
    return None


def apply(parser, section):
    """Seed `parser` defaults from [section] of the config file.

    Returns (path, applied) — path is None when no file was found, applied
    is the {dest: value} dict actually set, for the caller's startup line.
    Call BEFORE parse_args(); explicit CLI flags still override.
    """
    path = _find()
    if path is None:
        return None, {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"config: ignoring {path}: {e}", file=sys.stderr)
        return None, {}

    table = data.get(section)
    if table is None:
        return path, {}
    if not isinstance(table, dict):
        print(f"config: [{section}] in {path} is not a table", file=sys.stderr)
        return path, {}

    # Only optional flags are configurable — a positional (the shader path)
    # stays on the command line where it belongs.
    known = {a.dest: a for a in parser._actions if a.option_strings}
    applied = {}
    for key, val in table.items():
        dest = key.replace("-", "_")
        action = known.get(dest)
        if action is None:
            print(f"config: [{section}] unknown key {key!r} in {path} "
                  "(keys are the long flag names; see rayglow.example.toml)",
                  file=sys.stderr)
            continue
        if action.choices is not None and val not in action.choices:
            print(f"config: [{section}] {key} = {val!r} not one of "
                  f"{tuple(action.choices)} — skipped", file=sys.stderr)
            continue
        applied[dest] = val
    if applied:
        parser.set_defaults(**applied)
    return path, applied


def describe(path, section, applied):
    """The one-line startup print: where defaults came from and what they set."""
    kv = "  ".join(f"{k}={v}" for k, v in applied.items())
    return f"config: {path} [{section}]  {kv}"
