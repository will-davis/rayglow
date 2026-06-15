"""Prove rayglow/render/hub75.py is byte-identical to the rp2350b firmware packing.

The renderer (rayglow.render) packs each frame with `rayglow/render/hub75.py`,
a 1:1 port of the firmware's `Display::render` (firmware/src/lib.rs) + gamma LUT
(firmware/src/lut.rs).  This verifier closes the loop: it builds the Rust golden
(golden-frame/, which uses the firmware's own libm), then

  1. asserts the Python gamma LUT == the Rust golden LUT (bit-for-bit)
  2. loads the SAME deterministic input the Rust used (golden_input.bin)
  3. packs it with hub75.pack and asserts == golden_frame.bin (64 KB)

Run:  uv run --with numpy tools/verify.py     (needs `cargo` for the golden)
A green run means the wire format is locked end-to-end: whatever rayglow renders,
once packed, lands in the firmware's framebuffer exactly as render() would.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
GOLDEN = HERE / "golden-frame"
REPO_ROOT = HERE.parent

# The packer under test is the renderer's own hub75 module — host packer and
# deployed packer are the same file now that the firmware and renderer share a repo.
sys.path.insert(0, str(REPO_ROOT))
from rayglow.render import hub75 as fp  # noqa: E402


def build_and_run_golden() -> None:
    print("building + running Rust golden (firmware-identical libm)...")
    subprocess.run(
        ["cargo", "run", "--release", "--quiet"],
        cwd=GOLDEN,
        check=True,
    )


def main() -> int:
    build_and_run_golden()

    # 1. gamma LUT match
    rust_lut = np.fromfile(GOLDEN / "gamma_lut.bin", dtype="<u2")
    py_lut = fp.build_gamma_lut()
    if not np.array_equal(rust_lut, py_lut):
        diff = np.flatnonzero(rust_lut != py_lut)
        print(f"GAMMA LUT MISMATCH at {len(diff)} entries: {diff[:16]}")
        for i in diff[:16]:
            print(f"  idx {i:3d}: rust={rust_lut[i]:3d} py={py_lut[i]:3d}")
        return 1
    print(f"  gamma LUT: {len(py_lut)} entries identical ✓")

    # 2. load the exact input the golden used
    raw_in = np.fromfile(GOLDEN / "golden_input.bin", dtype=np.uint8)
    frame = raw_in.reshape(fp.WALL_H, fp.W, 3)

    # 3. pack and compare
    py_frame = fp.pack(frame)
    golden = (GOLDEN / "golden_frame.bin").read_bytes()

    if len(py_frame) != len(golden):
        print(f"SIZE MISMATCH: py={len(py_frame)} golden={len(golden)}")
        return 1

    a = np.frombuffer(py_frame, dtype="<u2")
    b = np.frombuffer(golden, dtype="<u2")
    if not np.array_equal(a, b):
        diff = np.flatnonzero(a != b)
        print(f"FRAME MISMATCH at {len(diff)}/{len(a)} cells: {diff[:16]}")
        for i in diff[:8]:
            print(f"  cell {i:5d}: py=0x{a[i]:04x} golden=0x{b[i]:04x}")
        return 1

    print(f"  packed frame: {len(golden)} bytes byte-identical ✓")
    print("\nALL GREEN — wire format locked (rayglow/render/hub75.py == firmware).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
