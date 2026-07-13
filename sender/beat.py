#!/usr/bin/env python3
"""Real-time beat tracker for the RayGLow sender (packet v3).

Clean-room numpy implementation of the causal beat-tracking model from:

  [1] Stark, Davies, Plumbley — "Real-time beat-synchronous analysis of
      musical audio", DAFx-09 (2009).
  [2] Davies, Plumbley — "Context-dependent beat tracking of musical
      audio", IEEE TASLP 15(3) (2007).

Algorithm from the papers, no code ported from GPL implementations (BTrack
et al.) — unlike sender.py's band path this file owes nothing to MilkDrop.

Runs at the sender's frame rate (~60 Hz), one onset-envelope sample per
frame — the auto-gained spectral flux sender.py already computes (~1.0
typical, spikes on attacks).  Three cooperating pieces:

  1. TEMPO INDUCTION (every RECOMPUTE_SECS): autocorrelate the mean-removed
     6 s onset history; score each candidate period with a 4-harmonic comb
     filterbank [2] x a Rayleigh prior centred on 120 BPM [2] x a
     log-Gaussian continuity weight around the current period (don't jump
     octaves without evidence); quadratic peak interpolation gives a
     sub-frame period, so BPM isn't quantized to integer frame lags.
  2. CUMULATIVE SCORE (every frame): C[m] = (1-ALPHA)*odf[m] +
     ALPHA*max(W2(v)*C[m+v]), v in [-2tau, -tau/2] [1] — a recursive trace
     of "where beats have consistently landed" that stays sharp through
     fills and breakdowns where the raw flux gets noisy.
  3. BEAT GRID, PLL-style (predictive, the v3 fix): the beat grid
     free-runs at the induced tempo; every recompute, a slew-limited
     correction (<= PHASE_SLEW periods) pulls it toward the comb alignment
     of the cumulative score.  Tempo from 1, phase from 2, no hard resets —
     v2's "halve the phase on a loud onset" heuristic is gone.
     beat_phase RAMPS 0 -> 1 and hits 1.0 ON the predicted beat (then
     wraps to 0), so shaders can anticipate a hit instead of reacting a
     frame late.  bar_phase counts a 4/4 bar the same way (downbeats are
     counted, not detected — honest only after a stable stretch).

Run `uv run beat.py` for the offline validation harness: synthetic click
tracks (jittered onsets + off-beat hats + noise floor) at several BPMs,
asserting tempo lock and predicted-beat phase error.
"""
import numpy as np


class BeatTracker:
    """Causal tempo + predictive beat phase from an onset envelope.

    Call update() once per frame; returns
    (bpm, beat_phase, bar_phase, conf, beat, downbeat).
    """

    MIN_BPM, MAX_BPM = 65.0, 190.0   # search range (margin past the musical 70-180)
    HIST_SECS = 6.0          # onset history / autocorrelation window
    RECOMPUTE_SECS = 0.5     # tempo induction + phase correction cadence
    ALPHA = 0.9              # cumulative score: past weight vs new onset [1]
    ETA = 5.0                # transition weight tightness [1]
    RAYLEIGH_BPM = 120.0     # tempo prior centre [2]
    CONT_SIGMA = 0.3         # tempo continuity sigma, ln-period units
    PHASE_SLEW = 0.15        # max phase correction per recompute (periods)
    COMB_HARMONICS = 4

    def __init__(self, fps):
        self.fps = float(fps)
        self.N = max(16, int(self.HIST_SECS * fps))
        self.odf = np.zeros(self.N, np.float64)
        self.C = np.zeros(self.N, np.float64)   # cumulative-score ring
        self.pos = 0                            # ring write index
        self.m = 0                              # frame counter
        self.filled = False
        self.tau = 60.0 * self.fps / 120.0      # beat period, frames (float)
        self.bpm = 120.0
        self.conf = 0.0
        self.beat_count = 0
        self.next_beat = self.tau               # frame index of next beat
        self._since_recompute = 0.0
        self._alt_streak = 0                    # consecutive alternation hits

    def update(self, onset, dt):
        """onset: auto-gained spectral flux (~1.0 typical).  Returns
        (bpm, beat_phase 0..1 predictive ramp, bar_phase 0..1 over 4 beats,
        confidence 0..1, beat_flag, downbeat_flag)."""
        self.odf[self.pos] = float(onset)
        self._cum_score(float(onset))

        self._since_recompute += dt
        if self._since_recompute >= self.RECOMPUTE_SECS:
            self._since_recompute = 0.0
            if self.filled or self.m >= self.N // 2:
                self._induct_tempo()
                self._align_phase()

        beat = downbeat = False
        if self.m >= self.next_beat:
            beat = True
            self.beat_count += 1
            downbeat = (self.beat_count % 4) == 0
            while self.next_beat <= self.m:     # never fall behind the present
                self.next_beat += self.tau

        phase = float(np.clip(1.0 - (self.next_beat - self.m) / self.tau,
                              0.0, 1.0))
        bar = ((self.beat_count % 4) + phase) / 4.0

        self.pos = (self.pos + 1) % self.N
        if self.pos == 0:
            self.filled = True
        self.m += 1
        return self.bpm, phase, bar, self.conf, beat, downbeat

    # -- internals ----------------------------------------------------------

    def _cum_score(self, onset):
        """C[m] per [1]: new onset blended with the best transition-weighted
        past score one beat-ish ago.  Written at self.pos (current frame)."""
        tau = self.tau
        v = np.arange(max(1, int(round(0.5 * tau))), int(round(2.0 * tau)) + 1)
        prev = self.C[(self.pos - v) % self.N]
        w2 = np.exp(-0.5 * (self.ETA * np.log(v / tau)) ** 2)
        self.C[self.pos] = ((1.0 - self.ALPHA) * onset
                            + self.ALPHA * float((w2 * prev).max()))

    def _ordered(self, ring):
        """Ring contents oldest-first (newest = current frame at self.pos).
        Before the ring first fills, only the frames actually written — a
        block of stale zeros biases the autocorrelation mean and fakes an
        alternation signature in the phase fold."""
        out = np.concatenate((ring[self.pos + 1:], ring[:self.pos + 1]))
        if not self.filled:
            out = out[-(self.m + 1):]
        return out

    def _induct_tempo(self):
        x = self._ordered(self.odf)
        x = x - x.mean()
        ac = np.correlate(x, x, mode="full")[len(x) - 1:]
        if ac[0] <= 1e-9:                      # silence: hold tempo, lose faith
            self.conf *= 0.9
            return
        ac = ac / ac[0]

        lo = int(np.ceil(60.0 * self.fps / self.MAX_BPM))
        hi = int(np.floor(60.0 * self.fps / self.MIN_BPM))
        hi = min(hi, (len(ac) - 1) // self.COMB_HARMONICS)
        if hi <= lo + 1:
            return
        lags = np.arange(lo, hi + 1)

        # 4-harmonic comb: a true period's multiples all land on ac peaks;
        # each harmonic reads its local max over a +-h/2 window (jitter slop).
        score = np.zeros(len(lags))
        for i, k in enumerate(lags):
            s = 0.0
            for h in range(1, self.COMB_HARMONICS + 1):
                a = h * k - h // 2
                b = h * k + h // 2 + 1
                s += float(ac[a:b].max()) / h
            score[i] = s

        beta = 60.0 * self.fps / self.RAYLEIGH_BPM      # 120 BPM in frames
        lf = lags.astype(np.float64)
        score *= (lf / beta ** 2) * np.exp(-lf ** 2 / (2.0 * beta ** 2))
        if self.conf > 0.0:   # continuity: only once we've locked at least once
            score *= np.exp(-np.log(lf / self.tau) ** 2
                            / (2.0 * self.CONT_SIGMA ** 2))

        j = int(np.argmax(score))
        k = int(lags[j])
        off = 0.0
        if 0 < j < len(lags) - 1:              # sub-frame period via parabola
            y0, y1, y2 = score[j - 1], score[j], score[j + 1]
            denom = y0 - 2.0 * y1 + y2
            if abs(denom) > 1e-12:
                off = float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5))
        tau = k + off

        # Half-period guard: beats + off-beat hats form a strong/weak
        # alternating train whose subdivision lag wins the comb (the Rayleigh
        # prior likes the faster level).  Fold the ODF over a 2*tau cycle by
        # PHASE (fraction-accurate — an integer-lag fold smears when tau is
        # fractional) and compare the two beat slots: if the opposite slot
        # carries well under half the peak's onset energy, we grabbed the
        # hats' level and the true beat period is 2*tau.  Range-guarded so a
        # misfire can never cascade below MIN_BPM (the observed 70->140->35
        # ratchet), and a uniform train (equal slots) never trips it.
        if 60.0 * self.fps / (2.0 * tau) >= self.MIN_BPM \
                and self._alternating(tau):
            self._alt_streak += 1
        else:
            self._alt_streak = 0
        if self._alt_streak >= 2:      # persistent, not a one-window blip
            k, tau = 2 * k, 2.0 * tau
            self._alt_streak = 0

        self.tau = tau
        self.bpm = 60.0 * self.fps / self.tau
        a, b = max(1, k - 1), min(len(ac), k + 2)
        raw_conf = float(np.clip(ac[a:b].max(), 0.0, 1.0))  # local peakiness
        self.conf += 0.3 * (raw_conf - self.conf)

    def _alternating(self, tau, nbins=16, ratio=0.45):
        """True if the ODF folded over a 2*tau cycle shows strong/weak
        alternation between its two beat slots (subdivision signature).
        The opposite slot reads a 3-bin neighborhood max — a beat straddling
        a bin edge otherwise reads artificially low and fakes alternation."""
        x = self._ordered(self.odf)
        ph = (np.arange(len(x)) / (2.0 * tau)) % 1.0
        bins = (ph * nbins).astype(int)
        counts = np.maximum(np.bincount(bins, minlength=nbins), 1)
        prof = np.bincount(bins, weights=x, minlength=nbins) / counts
        i = int(np.argmax(prof))
        floor = prof.mean()
        pa = prof[i] - floor
        opp = (i + nbins // 2) % nbins
        pb = max(prof[(opp + d) % nbins] for d in (-1, 0, 1)) - floor
        return pa > 1e-9 and pb / pa < ratio

    def _align_phase(self):
        """Slew-limited pull of the beat grid toward the comb alignment of
        the cumulative score (the PLL phase detector)."""
        tau_i = max(2, int(round(self.tau)))
        w = np.arange(tau_i)                   # candidate "last beat was w ago"
        scores = np.zeros(tau_i)
        for j in range(4):                     # 4 past beats, geometric decay
            idx = (self.pos - w - int(round(j * self.tau))) % self.N
            scores += self.C[idx] * (0.8 ** j)
        w_star = int(np.argmax(scores))

        target = self.m - w_star + self.tau    # next beat implied by alignment
        err = target - self.next_beat
        err = (err + 0.5 * self.tau) % self.tau - 0.5 * self.tau   # mod period
        max_step = self.PHASE_SLEW * self.tau
        self.next_beat += float(np.clip(err, -max_step, max_step))
        while self.next_beat <= self.m:
            self.next_beat += self.tau
        if self.next_beat > self.m + self.tau:     # keep phase in one period
            self.next_beat -= self.tau * int((self.next_beat - self.m)
                                             // self.tau)
            if self.next_beat <= self.m:
                self.next_beat += self.tau


# ---- offline validation harness ------------------------------------------


def _make_odf(bpm, fps, secs, seed):
    """Synthetic onset envelope: jittered beat spikes (~3.0) + off-beat hats
    (~0.7) + a noisy ~0.8 floor — the shape the AGC'd flux really has.
    Returns (odf, true beat positions in frames)."""
    rng = np.random.default_rng(seed)
    n = int(secs * fps)
    odf = rng.uniform(0.6, 1.0, n)
    period = 60.0 * fps / bpm
    beats = []
    b = period * rng.uniform(0.0, 1.0)
    while b < n - 2:
        j = b + rng.normal(0.0, 0.006 * fps)          # ~6 ms timing jitter
        i = int(round(j))
        if 0 <= i < n:
            odf[i] += rng.uniform(2.2, 3.2)
            odf[min(i + 1, n - 1)] += 0.8             # smeared attack tail
            beats.append(j)
        k = int(round(j + period / 2.0))              # off-beat hat
        if 0 <= k < n:
            odf[k] += rng.uniform(0.4, 0.9)
        b += period
    return odf, np.asarray(beats)


def _validate(bpm, fps=60.0, secs=40.0, seed=0):
    """Run the tracker over a synthetic click track; evaluate after warmup.
    Returns (locked_bpm, bpm_err_pct, median_phase_err_ms, conf)."""
    odf, true_beats = _make_odf(bpm, fps, secs, seed)
    bt = BeatTracker(fps)
    dt = 1.0 / fps
    predicted = []
    for m, onset in enumerate(odf):
        if bt.update(onset, dt)[4]:            # beat flag
            predicted.append(m)
    t_eval = secs * fps / 2.0                 # score the second half only
    predicted = np.asarray([p for p in predicted if p >= t_eval], np.float64)
    eval_beats = true_beats[true_beats >= t_eval]
    if len(predicted) == 0 or len(eval_beats) == 0:
        return bt.bpm, 100.0, 1e9, bt.conf
    errs = np.array([np.abs(predicted - tb).min() for tb in eval_beats])
    phase_ms = float(np.median(errs)) * 1000.0 / fps
    bpm_err = abs(bt.bpm - bpm) / bpm * 100.0
    return bt.bpm, bpm_err, phase_ms, bt.conf


if __name__ == "__main__":
    import sys

    FPS = 60.0
    cases = [70.0, 90.0, 120.0, 128.0, 170.0]
    print(f"beat.py harness — {FPS:.0f} Hz ODF, 40 s clicks, eval on 2nd half")
    print(f"{'true':>6} {'locked':>8} {'err%':>6} {'phase ms':>9} {'conf':>5}")
    failed = False
    for bpm in cases:
        locked, err, ph, conf = _validate(bpm, FPS)
        ok = err <= 2.0 and ph <= 60.0
        failed |= not ok
        print(f"{bpm:6.1f} {locked:8.2f} {err:6.2f} {ph:9.1f} {conf:5.2f}"
              f"  {'ok' if ok else 'FAIL'}")
    sys.exit(1 if failed else 0)
