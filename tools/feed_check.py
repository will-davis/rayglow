"""Prove the feature-packet contract: sender ⇄ receiver ⇄ fake_sender.

The wire between the desktop and the Pi is a struct with three independent
authors — sender/sender.py, rayglow/fake_sender.py (deliberately standalone)
and rayglow/feed/receiver.py — that must stay byte-identical.  tools/verify.py
locks the render→firmware frame; this locks the audio→render packet:

  1. asserts the v3 format string is IDENTICAL in all three files, and that
     the duplicated flywheel constants (ENV_TIERS, THETA_WRAP) match between
     sender and fake_sender (the fmt would catch a layout drift; these would
     drift silently)
  2. packs one packet per version (v0..v3) whose every float field is a
     unique sentinel (its own unpack-tuple index), runs it through the
     receiver's real parse path, and asserts every field lands at the right
     key with the right value/shape — including the band-major env/theta
     reshape and the v0 sub=bass fallback

Run:  uv run --with numpy tools/feed_check.py
      uv run --with numpy tools/feed_check.py --live   # bind :5005 and pretty-
                                                       # print real packets 2 Hz
The --live mode is the sine-tone band-verification tool: play a tone at a band
center (35/85/173/354/707/1581/3873/9798 Hz) and watch exactly one band bar
dominate.  (Dry-run renders never listen, so this is the desktop's only live
view of the parsed feed.)
"""

from __future__ import annotations

import argparse
import importlib.util
import struct
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from rayglow.feed import receiver  # noqa: E402
from rayglow import fake_sender  # noqa: E402


def _load_sender():
    """sender/sender.py is a standalone uv project, not a package — load it
    by path (its sibling import `beat` needs the directory on sys.path)."""
    sender_dir = REPO_ROOT / "sender"
    sys.path.insert(0, str(sender_dir))
    spec = importlib.util.spec_from_file_location("sender", sender_dir / "sender.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_contract(sender) -> int:
    fails = 0

    def ok(cond, what):
        nonlocal fails
        if cond:
            print(f"  {what} ✓")
        else:
            print(f"  {what} FAILED")
            fails += 1

    # 1. the three authors agree
    ok(sender.PACKET_FMT == receiver.PACKET_FMT_V3 == fake_sender.PACKET_FMT,
       "v3 fmt identical in sender / receiver / fake_sender")
    ok(sender.VERSION == fake_sender.VERSION == 3, "VERSION == 3 on both senders")
    ok(sender.ENV_TIERS == fake_sender.ENV_TIERS
       and abs(sender.THETA_WRAP - fake_sender.THETA_WRAP) < 1e-12,
       "flywheel constants (ENV_TIERS, THETA_WRAP) in lockstep")
    sizes = {v: s for v, (s, _f) in receiver.VERSIONS.items()}
    ok(sizes == {0: 556, 1: 564, 2: 4236, 3: 2996},
       f"size table {sizes}")

    # 2. sentinel roundtrip per version: every float = its unpack-tuple index
    flags = 0x02 | 0x10 | 0x20        # source_domain=2 + BEAT + DOWNBEAT
    for version, (size, fmt) in sorted(receiver.VERSIONS.items()):
        nvals = len(struct.unpack(fmt, bytes(size)))
        vals = [receiver.MAGIC, version, flags, 7, 4.0] + \
               [float(i) for i in range(5, nvals)]
        pkt = struct.pack(fmt, *vals)
        d = receiver._to_dict(struct.unpack(fmt, pkt), seq=7)

        ok(d["bass"] == 5.0 and d["vol"] == 11.0
           and d["source_domain"] == 2 and d["beat"] and d["downbeat"],
           f"v{version}: header + legacy bands + flags")

        if version == 0:
            ok(d["sub"] == d["bass"] and d["sub_att"] == d["bass_att"],
               "v0: sub falls back to bass")
            ok(np.array_equal(d["wave"], np.arange(12, 140, dtype=np.float32)),
               "v0: wave[128]")
        elif version == 1:
            ok(d["sub"] == 140.0 and d["sub_att"] == 141.0, "v1: trailing sub pair")
        elif version == 2:
            ok(d["sub"] == 12.0
               and np.array_equal(d["wave"], np.arange(14, 526, dtype=np.float32))
               and np.array_equal(d["spec"], np.arange(526, 1038, dtype=np.float32))
               and np.array_equal(d["chroma"], np.arange(1038, 1050, dtype=np.float32))
               and (d["centroid"], d["crest"]) == (1050.0, 1054.0)
               and (d["bpm"], d["beat_phase"], d["beat_conf"]) == (1055.0, 1056.0, 1057.0)
               and (d["width"], d["pan"]) == (1058.0, 1059.0),
               "v2: field placement")
        else:  # v3
            env_expect = np.arange(22, 46, dtype=np.float32).reshape(8, 3)
            theta_expect = np.arange(46, 70, dtype=np.float32).reshape(8, 3)
            ok(d["sub"] == 12.0 and d["sub_att"] == 13.0, "v3: legacy sub pair")
            ok(np.array_equal(d["bands"], np.arange(14, 22, dtype=np.float32)),
               "v3: band_imm[8]")
            ok(np.array_equal(d["band_env"], env_expect)
               and d["band_env"][2][1] == 22 + 2 * 3 + 1,
               "v3: band_env[8][3] band-major")
            ok(np.array_equal(d["band_theta"], theta_expect),
               "v3: band_theta[8][3] band-major")
            ok(np.array_equal(d["band_onset"], np.arange(70, 78, dtype=np.float32)),
               "v3: band_onset[8]")
            ok(d["vol_imm"] == 78.0
               and np.array_equal(d["vol_env"], np.arange(79, 82, dtype=np.float32))
               and np.array_equal(d["vol_theta"], np.arange(82, 85, dtype=np.float32)),
               "v3: vol block")
            ok(np.array_equal(d["wave"], np.arange(85, 597, dtype=np.float32))
               and np.array_equal(d["spec"], np.arange(597, 725, dtype=np.float32))
               and np.array_equal(d["chroma"], np.arange(725, 737, dtype=np.float32)),
               "v3: wave[512] / spec[128] / chroma[12]")
            ok((d["centroid"], d["flux"], d["flatness"], d["rolloff"], d["crest"])
               == (737.0, 738.0, 739.0, 740.0, 741.0),
               "v3: descriptors")
            ok((d["bpm"], d["beat_phase"], d["bar_phase"], d["beat_conf"])
               == (742.0, 743.0, 744.0, 745.0),
               "v3: beat block (incl. bar_phase)")
            ok((d["width"], d["pan"], d["key_idx"], d["key_conf"])
               == (746.0, 747.0, 748.0, 749.0),
               "v3: stereo + key")

    print("all green — the wire is locked" if fails == 0
          else f"{fails} CHECK(S) FAILED")
    return fails


def live(sender):
    """Bind the real Receiver and pretty-print parsed packets at 2 Hz."""
    rx = receiver.Receiver()
    print("listening on :%d — ctrl-c to stop" % 5005)

    def bar(v, scale=3.0, width=16):
        n = int(min(max(v / scale, 0.0), 1.0) * width)
        return ("█" * n).ljust(width)

    labels = [f"{int(lo)}-{int(hi)}" for lo, hi in
              zip(sender.BAND_EDGES_V3[:-1], sender.BAND_EDGES_V3[1:])]
    last, last_print = None, 0.0
    while True:
        pkt = rx.poll()
        if pkt is not None:
            last = pkt
        now = time.monotonic()
        if last is not None and now - last_print >= 0.5:
            last_print = now
            d = last
            print(f"\nseq={d['seq']} t={d['t']:.1f}s "
                  f"legacy: bass={d['bass']:.2f} mid={d['mid']:.2f} "
                  f"treb={d['treb']:.2f} sub={d.get('sub', 0):.2f}")
            if d.get("bands") is not None:
                for i in range(8):
                    e = d["band_env"][i]
                    print(f"  b{i} {labels[i]:>11} Hz |{bar(d['bands'][i])}| "
                          f"imm={d['bands'][i]:5.2f} env={e[0]:4.2f}/"
                          f"{e[1]:4.2f}/{e[2]:4.2f} on={d['band_onset'][i]:4.2f} "
                          f"th0={d['band_theta'][i][0]:6.1f}")
                print(f"  vol |{bar(d['vol_imm'])}| {d['vol_imm']:5.2f}   "
                      f"bpm={d['bpm']:5.1f} phase={d['beat_phase']:.2f} "
                      f"bar={d['bar_phase']:.2f} conf={d['beat_conf']:.2f}   "
                      f"key={sender.KEY_NAMES[int(d['key_idx'])]}"
                      f"/{d['key_conf']:.2f}")
        time.sleep(0.02)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="bind :5005 and pretty-print real packets")
    args = ap.parse_args()
    sender = _load_sender()
    if args.live:
        live(sender)
        return 0
    return check_contract(sender)


if __name__ == "__main__":
    sys.exit(main())
