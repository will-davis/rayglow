# KiCad Agent Tooling & Capacity

> Brief for the orchestrator agent on **what KiCad automation exists on this machine, what an agent can and cannot do with it, and how to divide the custom-HAT PCB work (PROJECT-PLAN §9) between agent and human.** The author wants to do **as much of the KiCad design with agent help as is realistically sound** — but values transparent, understood design over autopilot. Set expectations from the capability matrix below; do not promise autonomous "design me a PCB."

---

## 0. TL;DR for the orchestrator

- **Schematic capture is highly agent-amenable** for this board — it's four identical `SN74AHCT245` buffer blocks + connectors + power, i.e. textbook parametric repetition. Generate it as **circuit-as-code** (SKiDL) or by emitting/patching the `.kicad_sch` (kicad-sch-api), then verify with headless **ERC**.
- **PCB layout/routing is human-in-the-loop.** Agents can *place* footprints, run **DRC** in a loop, and **render the board to PNG** for visual review — but producing a *good* 2-layer routed HAT (controlled CLK length-matching, the scattered-BCM-header fan-out from §6) is a guided GUI job, not autonomous. Treat the agent as a co-pilot that scripts placement, checks rules, and renders for review.
- **Two of the agent's tools need the KiCad GUI open** (live IPC); the rest are **headless** (`kicad-cli`, file parsing) and run with no GUI.
- Everything here is **installed and proven** as of 2026-06-10 (KiCad 10.0.3).

---

## 1. Environment (verified present)

| Component | Version / location | Notes |
|---|---|---|
| KiCad | **10.0.3** | `/usr/bin/kicad`, GUI app |
| `kicad-cli` | **10.0.3** | `/usr/bin/kicad-cli`, headless — no GUI needed |
| Stock libraries | `kicad-library` + `kicad-library-3d` | **222 symbol libs, 155 footprint sets, 105 3D-model packs** at `/usr/share/kicad/{symbols,footprints,3dmodels}` |
| Host OS | CachyOS (Arch) on desktop | `pacman`; **fish** shell (no `<<` heredocs); **uv** for all Python |
| Python | via `uv` only | No pip/venv/conda — author preference, enforce it |

**Library gotcha (already resolved, noted for reproducibility):** on Arch the `kicad` package ships *only* the editors; the libraries are separate packages. They're installed now and the global library tables are populated. A fresh checkout on another machine needs `sudo pacman -S kicad-library kicad-library-3d`.

---

## 2. The `kicad-mcp` server (the agent's live interface)

A proof-of-concept **MCP server** wrapping KiCad for agent control. Read-only by design (POC).

- **Location:** `~/Projects/kicad-mcp/` (uv project; `server.py`, `connect_test.py`, `README.md`).
- **Stack:** `kicad-python` (`kipy`) 0.7.1 for the live IPC half; `kicad-cli` subprocess for the headless half; `mcp` 1.27 (FastMCP, stdio transport).
- **Registered in Claude Code** (local config, scoped to the kicad-mcp project dir). To make the `kicad` tools available from *any* directory, re-add with user scope:
  `claude mcp add kicad -s user -- uv --directory ~/Projects/kicad-mcp run python server.py`

### Tools exposed
| Tool | Half | Needs GUI? | Returns |
|---|---|---|---|
| `kicad_status` | live (kipy) | **yes** | connection up?, KiCad version, board open? |
| `list_footprints` | live | **yes** | per-footprint ref, value, X/Y mm, layer |
| `list_nets` | live | **yes** | net code + name |
| `board_summary` | live | **yes** | counts: footprints/pads/nets/tracks/vias/zones/layers |
| `run_drc` | headless (cli) | no | DRC violations on a `.kicad_pcb` (JSON-parsed) |
| `export_gerbers` | headless (cli) | no | plots Gerbers to a dir |

**Live tools require:** KiCad GUI running + **Preferences → Plugins → Enable IPC API server** + a board open in the PCB editor. The IPC API in KiCad 9/10 is **PCB-first and GUI-only**; headless IPC arrives in KiCad 11.

**Build-out path** (when ready to go beyond read-only): `kipy` write-commits — `board.begin_commit()` / `board.create_items()` / `board.push_commit()` — let an agent place and move footprints, draw the board outline, etc. The schematic side has *no* mature IPC; use the file/code surfaces in §3 instead.

---

## 3. The three automation surfaces (pick per task)

**A. Live IPC (`kipy`)** — talk to a running GUI. Good for *inspecting* the board an operator has open, and (with write-commits) scripted placement. PCB only. GUI must be up.

**B. Headless `kicad-cli`** — the robust, version-stable workhorse. No GUI. Full surface verified on this machine:
- `sch erc` — Electrical Rules Check (JSON or report).
- `sch export {netlist, bom, python-bom, pdf, svg}` — **`netlist` lets an agent verify connectivity programmatically**; `pdf`/`svg` for visual diff.
- `pcb drc` — Design Rules Check (JSON/report; supports `--schematic-parity`).
- `pcb export {gerbers, drill, pos, step, glb, svg, pdf, ipc2581, ipcd356, ...}` — full fab output.
- **`pcb render`** — board → **PNG/JPEG**. A vision-capable agent can render its own layout and critique placement/routing visually, headless.

**C. File-level / circuit-as-code** — `.kicad_sch` and `.kicad_pcb` are S-expression text.
- **SKiDL** (`uv add skidl`) — describe the circuit in Python, emit a netlist. **Best fit for this board's 4× repetition** — a loop, not four hand-placed copies.
- **kicad-sch-api** / **kiutils** — parse/generate/patch schematic files directly. Use when you need to produce or edit an actual `.kicad_sch`.
- Trade-off: you own correctness; there's no live connectivity engine — so **always gate file-level output through `kicad-cli sch erc`**.

---

## 4. Capability matrix — what the agent CAN / CANNOT do

**CAN (high confidence):**
- Generate the schematic from a pin/net spec (SKiDL or kicad-sch-api) — especially the regular '245 + connector + power structure.
- Run **ERC/DRC** headless and iterate until clean (great inner loop).
- Extract/diff the **netlist** to prove the board matches the intended §6 pin map.
- Export **Gerbers/drill/pos/STEP/BOM** for fab once the layout exists.
- **Render the board/schematic to image** for visual review by a vision model.
- Inspect a live board the operator has open (footprint positions, nets, counts).
- Script **footprint placement** via `kipy` write-commits (rough placement, arrays).

**CANNOT (today) / needs a human:**
- **Quality interactive routing** — especially the §6 reality that BCM numbering makes the 19 signals electrically contiguous but **physically scattered** across the header, plus §9 CLK length-matching and series-termination placement. This is a guided-GUI job.
- **Autonomous "good" 2-layer layout.** Agent placement + DRC ≠ a clean board. Human judgment on stack-up, copper pours, panel-power copper width (the 5 V rail feeds only the '245s here, but grounds are commoned — §9.3).
- **Drive the GUI** (menus, interactive tools). No GUI automation surface; live API is data-level only.
- **Schematic via IPC** — not supported; must go through file/code (§3C).
- Anything while a file is **open in the GUI** — external edits get clobbered on save (see §6).

---

## 5. Recommended division of labor for THIS HAT (PROJECT-PLAN §9)

The HAT is logically simple, physically fiddly. Suggested flow:

1. **Lock the net spec first.** The §6 GPIO map + §9 rules (per-connector buffered CLK/LAT/OE/ADDR fan-out, DIR tied, OE→GND, 100 nF per '245, ~22–33 Ω series on CLK/data, 4× HUB75 2×8 IDC, **panel 5 V NOT on the ribbon**, grounds commoned). This spec is the agent's input — without it, schematic gen is blocked (same blocker that left the `bit-hat-bang` exploratory sketch unwired).
2. **Agent: generate schematic** (SKiDL/kicad-sch-api) from that spec → `kicad-cli sch erc` until clean → `sch export pdf` for the author to eyeball. Remember **PWR_FLAG** on the 5 V and GND nets or ERC will complain about undriven power inputs.
3. **Human + agent: footprint assignment** — agent proposes (it knows the §1 footprints: `SOIC-20W` for the DWR '245s, `Connector_IDC:IDC-Header_2x08` keyed box headers for HUB75, `PinSocket_2x20` for the Pi header), human confirms.
4. **Human-led layout** in the GUI; agent assists by scripting initial placement (`kipy` commits), running `pcb drc` in a loop, and `pcb render` for visual review between passes.
5. **Agent: fab outputs** — Gerbers/drill/pos/BOM/STEP once DRC is clean.

**Realistic expectation to set with the author:** steps 1–3, 5 are largely agent-driven; step 4 is collaborative with the human holding the routing pen. That still offloads the majority of the tedium.

---

## 6. Hard constraints & gotchas (read before touching files)

- **GUI lock files.** A project open in KiCad writes `~*.lck` / `.kicad_prl`. **Never edit a `.kicad_sch`/`.kicad_pcb` on disk while it's open** — the GUI overwrites on save. Either close the GUI for file-level work, or use live IPC (which is GUI-safe).
- **Footprint cache vs library.** Boards embed a *copy* of each footprint (self-containment). `lib_footprint_mismatch` DRC warnings just mean the cached copy drifted from the library master → `Tools → Update Footprints from Library`. Benign.
- **PWR_FLAG.** KiCad ERC wants every power-*input* pin driven by a power-*output*; real sources (headers, jacks) aren't typed that way. Drop a `power:PWR_FLAG` on the 5 V and GND nets to assert "power enters here."
- **Format-version coupling.** `kicad-cli` and the file format are tied to the installed KiCad (10.0.3). Hand-written `.kicad_pcb`/`.kicad_sch` must match the format version or the CLI rejects them — prefer library-emitting tools (SKiDL/kicad-sch-api) over raw S-expr authoring.
- **`kipy` is young/fast-moving.** Pinned at 0.7.1. If it's bumped, re-introspect the API (footprint ref is `fp.reference_field.text.value`; `position` is a `Vector2` in **nm**, ÷1e6 → mm; `Net.name`/`.code`).

---

## 7. Pointers

- MCP server + verified API notes: `~/Projects/kicad-mcp/` (`README.md`, `server.py`).
- Exploratory scratch schematic (4× 74HCT245, 40-pin J1, 4× 16-pin J2–J5, **unwired** — fine to discard or reuse as a symbol-placement starting point): `~/Projects/bit-hat-bang/bit-hat-bang/`.
- Prior-art KiCad MCP servers worth reading before building out write-ops:
  `Seeed-Studio/kicad-mcp-server` (pin-level tracing), `circuit-synth/mcp-kicad-sch-api` (schematic gen), `oaslananka/kicad-mcp-pro` (validation gates/DFM/export), `lamaalrajih/kicad-mcp` (broad reference).
- This board's electrical intent: **PROJECT-PLAN.md §6 (pin map) and §9 (HAT scope)** — the schematic-gen agent's source of truth.
