#!/usr/bin/env python3
"""Prove the remote-render frame-link wire contract: net_out (render host)
⇄ link.py fragments/credits ⇄ framesink (Pi display loop).

tools/feed_check.py locks the audio→render packet, verify.py the
render→firmware frame, control_check.py the control channel; this locks the
fourth cross-machine contract — rendered frames, render host → Pi.  It runs
with no GPU, no panel, and no network beyond loopback.

What it checks:
  1. layout pins — header sizes and magics can't drift silently
  2. fragment → reassemble round-trips byte-identical, at any chunk size,
     with fragments arriving in any order
  3. latest-wins under loss: an incomplete frame is abandoned the moment a
     newer one completes, and stale fragments after that are ignored
  4. seq compare survives uint32 wraparound (RFC1982, as the feed)
  5. the credit ledger — the window blocks at N in flight, absolute counters
     heal lost credit datagrams, reset() grants a fresh window
  6. end-to-end over loopback UDP: NetOut → framesink.serve() (null backend)
     — every displayed frame byte-identical to what was sent, and the
     producer PACED to the sink's rate with bounded inflight (the §5.5
     bufferbloat trap, tested explicitly per plan Phase 2)
  7. a sender with no sink alive times out, resets, and keeps going —
     no deadlock

Run: uv run --with numpy tools/link_check.py
"""
import os
import random
import socket
import sys
import threading
import time
import zlib

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)                     # import rayglow.*

from rayglow import framesink, link           # noqa: E402
from rayglow.render.net_out import NetOut     # noqa: E402


def check_layout():
    assert link.FRAG_HDR == 24 and link.CREDIT_SIZE == 32
    hdr, payload = next(link.fragment(b"\0" * 12, 2, 2, seq=7, chunk=64))
    assert hdr[:4] == b"RGLF" and len(hdr) == 24, hdr
    assert bytes(payload) == b"\0" * 12
    cred = link.pack_credit(7, 1, 2, 3, 4)
    assert cred[:4] == b"RGLC" and len(cred) == 32
    got = link.unpack_credit(cred)
    assert got == {"seq_shown": 7, "shown": 1, "skipped": 2, "dropped": 3,
                   "window": 4}, got
    assert link.unpack_credit(cred[:-1]) is None          # wrong size
    assert link.unpack_credit(b"XXXX" + cred[4:]) is None  # wrong magic
    print("  layout: 24 B fragment header, 32 B credit, magics pinned")


def _dgrams(frame, w, h, seq, chunk):
    return [hdr + bytes(pl) for hdr, pl in
            link.fragment(frame, w, h, seq, chunk)]


def check_roundtrip():
    rng = np.random.default_rng(42)
    r = link.Reassembler()
    for seq, chunk in ((1, 1448), (2, 8948), (3, 977), (4, 147456)):
        w, h = 384, 128
        frame = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        ds = _dgrams(frame, w, h, seq, chunk)
        random.Random(seq).shuffle(ds)         # arrival order is not a contract
        done = [out for out in map(r.feed, ds) if out]
        assert len(done) == 1, f"seq {seq}: {len(done)} completions"
        gseq, gw, gh, buf = done[0]
        assert (gseq, gw, gh) == (seq, w, h)
        assert bytes(buf) == frame.tobytes(), f"seq {seq}: bytes differ"
    assert r.bad == r.dup == r.dropped == 0, (r.bad, r.dup, r.dropped)
    print("  roundtrip: byte-identical at chunk 1448/8948/977/whole-frame, "
          "shuffled arrival")


def check_latest_wins():
    r = link.Reassembler()
    a = _dgrams(b"\xAA" * 3072, 32, 32, 10, 512)
    b = _dgrams(b"\xBB" * 3072, 32, 32, 11, 512)
    lost = a.pop(3)                     # frame 10 loses a fragment
    for d in a:
        assert r.feed(d) is None
    done = [out for out in map(r.feed, b) if out]
    assert len(done) == 1 and done[0][0] == 11
    assert r.dropped == 1               # 10 abandoned when 11 completed
    assert r.feed(lost) is None and r.stale == 1   # too late — ignored
    # duplicates don't double-complete
    r2 = link.Reassembler()
    ds = _dgrams(b"\xCC" * 3072, 32, 32, 1, 512)
    done = [out for out in map(r2.feed, ds + ds) if out]
    assert len(done) == 1 and r2.dup + r2.stale == len(ds)
    print("  latest-wins: lost fragment costs exactly its frame; dups/stale "
          "ignored")


def check_wraparound():
    r = link.Reassembler()
    hi = 0xFFFFFFFF
    out = None
    for d in _dgrams(b"\x01" * 300, 10, 10, hi, 128):
        out = r.feed(d)
    assert out and out[0] == hi
    for d in _dgrams(b"\x02" * 300, 10, 10, 1, 128):   # next_seq skips 0
        out = r.feed(d)
    assert out and out[0] == 1, "post-wrap seq 1 must be newer than 2^32-1"
    assert link.next_seq(hi) == 1
    lg = link.CreditLedger(2)
    lg.seq_sent = hi
    assert lg.take_seq() == 1
    lg.note_credit({"seq_shown": hi, "shown": 0, "skipped": 0, "dropped": 0,
                    "window": 0})
    assert lg.inflight() == 1           # seq 1 is one ahead of 2^32-1
    print("  wraparound: seq compare and inflight math survive 2^32")


def check_ledger():
    lg = link.CreditLedger(2)
    assert lg.can_send() and lg.inflight() == 0 and lg.tokens == 2
    s1, s2 = lg.take_seq(), lg.take_seq()
    assert (s1, s2) == (1, 2) and lg.inflight() == 2 and not lg.can_send()
    # the credit for s1 was LOST; s2's alone heals both (absolute counters)
    lg.note_credit({"seq_shown": s2, "shown": 2, "skipped": 0, "dropped": 0,
                    "window": 0})
    assert lg.inflight() == 0 and lg.tokens == 2 and lg.can_send()
    # a skip eats a token — that trim is what stops render-2x-show-half —
    # but the flip floor keeps the next send allowed
    s3, s4 = lg.take_seq(), lg.take_seq()
    lg.note_credit({"seq_shown": s4, "shown": 3, "skipped": 1, "dropped": 0,
                    "window": 0})
    assert lg.tokens == 1 and lg.can_send(), (lg.tokens, lg.inflight())
    # a reported drop (lost fragments) replenishes — no token leak
    s5 = lg.take_seq()
    assert lg.tokens == 0
    lg.note_credit({"seq_shown": s5, "shown": 3, "skipped": 1, "dropped": 1,
                    "window": 0})
    assert lg.tokens == 1, lg.tokens
    # sink advertises its own window
    lg.note_credit({"seq_shown": s5, "shown": 3, "skipped": 1, "dropped": 1,
                    "window": 1})
    lg.take_seq()
    assert not lg.can_send(), "advertised window=1 must gate at 1"
    # silent sink: reset grants a fresh window instead of deadlocking
    lg.reset()
    assert lg.can_send()
    # a restarted RENDERER against a warm sink: counters jump -> clean slate
    lg2 = link.CreditLedger(2)
    lg2.take_seq()
    lg2.note_credit({"seq_shown": 50000, "shown": 50000, "skipped": 3,
                     "dropped": 1, "window": 0})
    assert lg2.tokens == 2 and lg2.can_send()
    print("  ledger: flips replenish, skips trim bursts, drops don't leak, "
          "absolute credits heal loss, restarts resync")


def check_sender_restart():
    r = link.Reassembler()
    done = [o for o in map(r.feed, _dgrams(b"\x01" * 3072, 32, 32, 50000, 512))
            if o]
    assert len(done) == 1 and r.seq_done == 50000
    # A restarted sender is back at seq 1 — stale to a warm sink.  A run of
    # STALE_RESET stale fragments wipes history and adopts the new numbering.
    frame1 = bytes(300)                       # chunk 1 => 300 stale frags
    for d in _dgrams(frame1, 10, 10, 1, 1):
        r.feed(d)
    assert r.restarts == 1, r.restarts
    done = [o for o in map(r.feed, _dgrams(b"\x02" * 300, 10, 10, 2, 64)) if o]
    assert len(done) == 1 and done[0][0] == 2
    print("  sender restart: warm sink resyncs after a stale run "
          f"(STALE_RESET={link.STALE_RESET} fragments)")


def check_end_to_end():
    pace = 240.0                        # a fast pretend display
    frames = 40
    w, h = 64, 32
    sink_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink_sock.bind(("127.0.0.1", 0))
    port = sink_sock.getsockname()[1]
    out = framesink.NullOut(pace)
    shown, stop = [], threading.Event()
    t = threading.Thread(
        target=framesink.serve, args=(sink_sock, out),
        kwargs=dict(window=2, stop=stop, quiet=True,
                    on_frame=lambda s, fw, fh, b: shown.append(
                        (s, zlib.crc32(bytes(b))))),
        daemon=True)
    t.start()

    rng = np.random.default_rng(7)
    net = NetOut("127.0.0.1", port, window=2, mtu=1500, timeout=2.0)
    sent = {}
    max_inflight = 0
    t0 = time.perf_counter()
    for _ in range(frames):
        frame = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        net.blit(frame)
        sent[net.ledger.seq_sent] = zlib.crc32(frame.tobytes())
        max_inflight = max(max_inflight, net.ledger.inflight())
    elapsed = time.perf_counter() - t0
    time.sleep(0.1)                     # let the last flips land
    stop.set()
    t.join(2.0)
    net.close()
    sink_sock.close()

    assert net.stalls == 0, "credit timeouts on loopback"
    for seq, crc in shown:
        assert sent.get(seq) == crc, f"frame {seq} corrupted in flight"
    assert len(shown) >= frames - 4, f"only {len(shown)}/{frames} displayed"
    # THE bufferbloat check (plan §5.5): with window 2 the producer must run
    # at the sink's pace, not free-run, and inflight stays bounded.
    floor = (frames - 4) / pace
    assert elapsed > floor, f"not paced: {frames} frames in {elapsed * 1e3:.0f}ms " \
                            f"(a paced run needs >{floor * 1e3:.0f}ms)"
    assert max_inflight <= 2, f"inflight hit {max_inflight} with window 2"
    # And the flip-token discipline: an instant renderer must converge on one
    # send per flip, not render 2x and have the sink skip half.
    final_skip = (net.ledger.last_credit or {}).get("skipped", 0)
    assert final_skip <= 2, f"sink skipped {final_skip} frames — the sender " \
                            "is bursting past the flip cadence"
    print(f"  end-to-end: {len(shown)}/{frames} frames byte-identical over "
          f"loopback, paced to {frames / elapsed:.0f} fps by a {pace:g} Hz "
          f"sink, inflight <= 2, {final_skip} skipped")


def check_no_sink():
    lonely = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lonely.bind(("127.0.0.1", 0))       # allocate a port, then leave it dead
    port = lonely.getsockname()[1]
    lonely.close()
    net = NetOut("127.0.0.1", port, window=2, timeout=0.05)
    frame = np.zeros((8, 8, 3), np.uint8)
    t0 = time.perf_counter()
    for _ in range(5):                  # window+3: must block, reset, proceed
        net.blit(frame)
    took = time.perf_counter() - t0
    assert net.stalls >= 1, "never timed out?"
    assert took < 2.0, f"deadlocked-ish: {took:.1f}s for 5 sends"
    net.close()
    print(f"  no-sink: {net.stalls} timeout resets, sender kept going "
          "(no deadlock)")


def main():
    print("frame-link contract check")
    check_layout()
    check_roundtrip()
    check_latest_wins()
    check_wraparound()
    check_ledger()
    check_sender_restart()
    check_end_to_end()
    check_no_sink()
    print("OK — frame-link wire contract holds")


if __name__ == "__main__":
    main()
