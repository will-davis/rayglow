"""The rendered-frame link: wire protocol for remote render (REMOTE-RENDER-PLAN.md).

The renderer (any host with a GPU — ubuntu-server's RTX 4080 in production)
fragments each resolved RGB frame over UDP to the Pi's framesink, which blits
it into the DPI framebuffer and answers one small credit datagram per page
flip.  Both ends import this module and nothing else changes hands, so this
file IS the contract — tools/link_check.py locks it.

Design, all inherited from the feed's philosophy (feed/receiver.py):

- UDP + latest-wins, never TCP: a lost packet must cost one frame, not a
  retransmit stall.  Per-frame seq + per-fragment index; incomplete frames
  are abandoned the moment a newer one completes.
- Credit-based flow control (plan §5.5): the sink's page-flip event is the
  master clock.  Credits carry ABSOLUTE counters (highest seq displayed),
  not increments, so a lost credit heals on the next one and end-to-end
  latency stays bounded at window x flip-period by construction.
- Sequence numbers wrap uint32 and compare RFC1982-style, same as the feed.

Frame fragment datagram (renderer -> sink, little-endian, 24 B header):

    offset  type     field
    0       uint32   magic = 0x464C4752 ("RGLF")
    4       uint16   version = 1
    6       uint16   w            (frame pixel width)
    8       uint16   h            (frame pixel height)
    10      uint16   frag_idx     (0-based)
    12      uint16   frag_cnt
    14      uint16   flags (reserved)
    16      uint32   seq          (frame number; wraps, never 0)
    20      uint32   offset       (this payload's byte offset into the frame)
    24      payload  (raw RGB888 bytes; frame total = w*h*3)

Carrying an explicit byte offset (not idx*chunk) means the receiver never
assumes the sender's chunk size — MTU 1500 and 9000 senders interoperate.

Credit datagram (sink -> renderer, little-endian, 32 B):

    offset  type     field
    0       uint32   magic = 0x434C4752 ("RGLC")
    4       uint16   version = 1
    6       uint16   flags (reserved)
    8       uint32   seq_shown    (highest frame seq blitted; 0 = none yet)
    12      uint32   shown        (total frames blitted)
    16      uint32   skipped      (complete but superseded before display)
    20      uint32   dropped      (abandoned incomplete — lost fragments)
    24      uint32   window       (frames in flight the sink allows; 0 = no opinion)
    28      uint32   reserved
"""
import struct

FRAG_MAGIC = 0x464C4752      # "RGLF" as little-endian bytes
CREDIT_MAGIC = 0x434C4752    # "RGLC"
VERSION = 1

_FRAG = struct.Struct("<IHHHHHHII")
_CREDIT = struct.Struct("<IHHIIIIII")
FRAG_HDR = _FRAG.size
CREDIT_SIZE = _CREDIT.size
assert FRAG_HDR == 24 and CREDIT_SIZE == 32

# Sanity bounds — a corrupt header must not allocate gigabytes.
MAX_FRAGS = 1024
MAX_FRAME_BYTES = 16 << 20

# A restarted RENDERER starts back at seq 1, which a warm sink would reject
# as stale forever (RFC1982 says 1 is older than 50000).  This many
# consecutive stale fragments — a couple of full frames' worth even at MTU
# 1500, far more than any real late-reordered packet burst — means the peer
# restarted: the reassembler wipes its history and adopts the new numbering.
STALE_RESET = 256

_M32 = 0xFFFFFFFF


def seq_newer(a, b):
    """True if seq a is newer than b under uint32 wraparound (RFC1982-style,
    same rule as the feed receiver)."""
    return ((a - b) & _M32) < 0x80000000 and a != b


def seq_delta(a, b):
    """Signed a-b under uint32 wraparound (how far ahead a is of b)."""
    return ((a - b + 0x80000000) & _M32) - 0x80000000


def next_seq(seq):
    """seq+1, wrapping past 0 (0 is the 'nothing yet' sentinel)."""
    seq = (seq + 1) & _M32
    return seq or 1


def fragment(frame, w, h, seq, chunk):
    """Yield (header, payload) datagram pairs for one frame.

    `frame` is the w*h*3 RGB bytes (anything memoryview-able); `chunk` is the
    max payload per datagram — pass them to socket.sendmsg([header, payload])
    so the frame bytes are never copied per-fragment.
    """
    # cast('B') flattens to bytes no matter what backs the buffer — a numpy
    # (H, W, 3) frame's plain memoryview would slice ROWS, not bytes.
    mv = memoryview(frame).cast("B")
    total = len(mv)
    cnt = -(-total // chunk)                      # ceil
    for i in range(cnt):
        off = i * chunk
        hdr = _FRAG.pack(FRAG_MAGIC, VERSION, w, h, i, cnt, 0, seq, off)
        yield hdr, mv[off:off + chunk]


def pack_credit(seq_shown, shown, skipped, dropped, window):
    return _CREDIT.pack(CREDIT_MAGIC, VERSION, 0, seq_shown & _M32,
                        shown & _M32, skipped & _M32, dropped & _M32,
                        window & _M32, 0)


def unpack_credit(dgram):
    """Parse a credit datagram -> dict, or None if it isn't one."""
    if len(dgram) != CREDIT_SIZE:
        return None
    magic, version, _flags, seq_shown, shown, skipped, dropped, window, _r = \
        _CREDIT.unpack(dgram)
    if magic != CREDIT_MAGIC or version != VERSION:
        return None
    return {"seq_shown": seq_shown, "shown": shown, "skipped": skipped,
            "dropped": dropped, "window": window}


class Reassembler:
    """Latest-wins fragment reassembly (the sink side).

    feed() one datagram at a time; returns (seq, w, h, buf) when a frame
    completes, else None.  `buf` is a bytearray of exactly w*h*3 bytes, no
    longer referenced here — np.frombuffer(...).reshape(h, w, 3) it without a
    copy.  On completion every OLDER pending frame is abandoned (that is the
    latest-wins: a lost fragment costs its frame, nothing waits for it), and
    stale fragments for already-passed seqs are ignored.
    """

    def __init__(self, max_pending=4):
        self.max_pending = max_pending
        self.pending = {}            # seq -> [buf, got_idx_set, filled, cnt, w, h]
        self.seq_done = 0            # highest completed seq (0 = none)
        self.frags = 0               # accepted fragments
        self.bad = 0                 # unparseable/inconsistent datagrams
        self.dup = 0                 # duplicate fragments
        self.stale = 0               # fragments for seqs already passed
        self.completed = 0
        self.dropped = 0             # frames abandoned incomplete
        self.restarts = 0            # sender-restart resyncs (STALE_RESET)
        self._stale_run = 0

    def feed(self, dgram):
        if len(dgram) < FRAG_HDR:
            self.bad += 1
            return None
        magic, version, w, h, idx, cnt, _flags, seq, off = \
            _FRAG.unpack_from(dgram)
        total = w * h * 3
        n = len(dgram) - FRAG_HDR
        if (magic != FRAG_MAGIC or version != VERSION or seq == 0
                or not 1 <= cnt <= MAX_FRAGS or idx >= cnt
                or not 0 < total <= MAX_FRAME_BYTES or off + n > total):
            self.bad += 1
            return None
        if self.seq_done and not seq_newer(seq, self.seq_done):
            self.stale += 1
            self._stale_run += 1
            if self._stale_run < STALE_RESET:
                return None
            self.pending.clear()     # peer restarted: adopt its numbering
            self.seq_done = 0
            self.restarts += 1
        self._stale_run = 0

        entry = self.pending.get(seq)
        if entry is None:
            entry = self.pending[seq] = [bytearray(total), set(), 0, cnt, w, h]
            if len(self.pending) > self.max_pending:
                oldest = min(self.pending, key=lambda s: seq_delta(s, seq))
                del self.pending[oldest]
                self.dropped += 1
        elif (cnt, w, h) != (entry[3], entry[4], entry[5]):
            self.bad += 1
            return None
        buf, got, filled, _, _, _ = entry
        if idx in got:
            self.dup += 1
            return None
        buf[off:off + n] = dgram[FRAG_HDR:]
        got.add(idx)
        entry[2] = filled + n
        self.frags += 1
        if len(got) < cnt:
            return None
        del self.pending[seq]
        if entry[2] != total:               # overlapping/gapped chunking
            self.bad += 1
            return None
        # Complete: everything older is now dead weight — abandon it.
        for s in [s for s in self.pending if not seq_newer(s, seq)]:
            del self.pending[s]
            self.dropped += 1
        self.seq_done = seq
        self.completed += 1
        return seq, w, h, buf


class CreditLedger:
    """Sender-side flow control off the sink's absolute credit counters.

    Two rules, and both must hold to send:

    - a TOKEN per sink page flip: tokens start at `window`, one is spent per
      send, and the sink's `shown` counter replenishes them — deltas of an
      absolute total, not increments, so a lost credit datagram heals on the
      next one.  Capped at `window`; that cap plus flip-sourced replenishment
      is what makes the sink's flip the master clock: after the startup
      burst the sender moves exactly one frame per flip, so a fast renderer
      can never settle into rendering frames the sink will just skip
      (rendering 2x and showing half would be the plan's SW5=8 waste,
      reinvented in software).  A skipped frame deliberately does NOT
      replenish — that skip means we oversupplied, and eating the token is
      what narrows the burst to the flip cadence; the floor below keeps
      liveness.  `dropped` (reported incomplete frames) does replenish, so
      fragment loss doesn't leak tokens.
    - inflight = seq_sent - seq_shown < window: the belt to the tokens'
      suspenders, and what bounds end-to-end latency at window x flip-period.

    A frame the sink never sees AT ALL (every fragment lost) replenishes
    nothing — rare on a switched LAN, and the sender's credit timeout
    (reset(): fresh tokens, clean slate) is the backstop for that and for
    sink restarts, degrading to a slow probe instead of deadlocking.
    """

    def __init__(self, window=2):
        self.window = window
        self.tokens = window
        self.seq_sent = 0            # last seq handed out (0 = none)
        self.seq_shown = 0           # sink's highest displayed
        self.last_credit = None

    def note_credit(self, cred):
        if cred is None:
            return
        if cred["window"]:           # the sink's opinion wins if it has one
            self.window = cred["window"]
        prev = self.last_credit
        self.last_credit = cred
        # A fresh sink's counters start at 0, so the no-prior baseline is 0
        # (a fresh SENDER against a warm sink lands in the resync branch).
        d_shown = seq_delta(cred["shown"],
                            prev["shown"] if prev else 0)
        d_drop = seq_delta(cred["dropped"],
                           prev["dropped"] if prev else 0)
        if 0 <= d_shown <= 16 * self.window and 0 <= d_drop <= 16 * self.window:
            self.tokens = min(self.window, self.tokens + d_shown + d_drop)
            if d_shown > 0:
                # The sink is flipping: always allow at least the next frame.
                # Without this floor a startup skip would retire a window
                # slot forever; with it, skips trim bursts and nothing else.
                self.tokens = max(self.tokens, 1)
        else:                        # counters jumped — sink restarted
            self.tokens = self.window
            self.seq_shown = self.seq_sent
        if seq_newer(cred["seq_shown"], self.seq_shown):
            self.seq_shown = cred["seq_shown"]

    def inflight(self):
        if not self.seq_sent:
            return 0
        return max(0, seq_delta(self.seq_sent, self.seq_shown))

    def can_send(self):
        return self.tokens >= 1 and self.inflight() < self.window

    def take_seq(self):
        self.tokens -= 1
        self.seq_sent = next_seq(self.seq_sent)
        return self.seq_sent

    def reset(self):
        self.tokens = self.window
        self.seq_shown = self.seq_sent
