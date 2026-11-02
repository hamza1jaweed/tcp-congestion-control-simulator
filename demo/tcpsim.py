"""
tcpsim.py - Packet-level discrete-event simulator for studying TCP congestion
control under different network conditions (CBIT CN Case Study 8).

Model
-----
* One or more TCP senders share a single bottleneck link (dumbbell topology).
* Bottleneck: FIFO drop-tail queue of B packets, time-varying capacity C(t),
  time-varying base round-trip propagation delay RTT(t) and an independent
  random (non-congestive, e.g. wireless) loss probability p(t).
* Receiver: cumulative ACKs, one ACK per data packet, out-of-order buffering.
  ACKs echo the send timestamp of the packet that triggered them (RFC 7323 style)
  so RTT samples are unambiguous even after retransmissions.
* Sender: per-packet sequence numbers (1 packet = 1 MSS = 1500 bytes),
  RFC 6298 retransmission timer (min RTO 200 ms, exponential back-off),
  fast retransmit on 3 duplicate ACKs.

Congestion-control algorithms
-----------------------------
SS     : Slow-Start only. Exponential growth, no ssthresh / congestion avoidance;
         any loss resets cwnd to 1 and slow start restarts.
AIMD   : Additive increase (1 MSS / RTT) from the first packet, multiplicative
         decrease (cwnd/2) on any loss signal, fast retransmit, no slow start.
TAHOE  : Slow start + congestion avoidance; 3 dup-ACKs or timeout ->
         ssthresh = flight/2, cwnd = 1 (go-back-N).
RENO   : As Tahoe, but 3 dup-ACKs -> fast retransmit + fast recovery
         (cwnd = ssthresh + 3, inflation per dup-ACK, deflation on new ACK).
DLA    : Proposed Delay- and Loss-Aware adaptive mechanism (see DLA section).
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque

MSS_BYTES = 1500
MSS_BITS = MSS_BYTES * 8
MIN_RTO = 0.2
MAX_RTO = 60.0
MAX_CWND = 20000.0


# --------------------------------------------------------------------------
# Network conditions
# --------------------------------------------------------------------------
class Schedule:
    """Piece-wise constant schedule: [(t_start, value), ...] sorted by time."""

    def __init__(self, points):
        if not isinstance(points, (list, tuple)):
            points = [(0.0, points)]
        self.points = sorted(points)

    def __call__(self, t):
        v = self.points[0][1]
        for ts, val in self.points:
            if t >= ts:
                v = val
            else:
                break
        return v


class Bottleneck:
    def __init__(self, cap_mbps, rtt_ms, loss_p, buffer_pkts, rng):
        self.cap = Schedule(cap_mbps)        # Mbps
        self.rtt = Schedule(rtt_ms)          # ms (base round-trip propagation)
        self.loss = Schedule(loss_p)         # probability per data packet
        self.B = int(buffer_pkts)
        self.rng = rng
        self.busy_until = 0.0
        self.departures = deque()            # departure times of queued pkts
        self.last_arrival = 0.0
        self.queue_drops = 0
        self.random_drops = 0
        self.qlen_samples = []

    def enqueue(self, now):
        """Returns receiver arrival time, or None if the packet is lost."""
        dq = self.departures
        while dq and dq[0] <= now:
            dq.popleft()
        if len(dq) >= self.B:
            self.queue_drops += 1
            return None
        start = max(now, self.busy_until)
        depart = start + MSS_BITS / (self.cap(start) * 1e6)
        self.busy_until = depart
        dq.append(depart)
        if self.rng.random() < self.loss(depart):
            self.random_drops += 1
            return None
        arr = depart + self.rtt(depart) / 2000.0
        if arr < self.last_arrival:          # keep FIFO when RTT shrinks
            arr = self.last_arrival
        self.last_arrival = arr
        return arr


# --------------------------------------------------------------------------
# DLA parameters (proposed mechanism)
# --------------------------------------------------------------------------
DLA_DEFAULT = dict(
    ss_exit=True,       # C1: delay-aware slow-start exit
    adaptive_ai=True,   # C2: adaptive additive increase
    early_backoff=True, # C3: delay-triggered early back-off
    loss_diff=True,     # C4: loss differentiation + bandwidth-estimate decrease
    partial_ack=True,   # C5: partial-ACK (NewReno-style) recovery
    d_ss=0.25,          # normalised queueing delay that ends slow start
    d_target=0.10,      # below this, the path is treated as under-utilised
    alpha_max=4.0,      # max additive increase (MSS per RTT)
    d_high=0.50,        # early back-off threshold
    beta_eb=0.85,       # early back-off factor
    d_rand=0.20,        # loss with d < d_rand classified as non-congestive
    beta_rand_max=0.90, # upper bound of reduction factor for random loss
    minrtt_win=10.0,    # seconds, window for min-RTT filter
    compete_guard=True, # suspend early back-off when it fails to drain the queue
    eb_max=3,           # consecutive non-draining back-offs before suspension
    compete_hold=10.0,  # seconds the back-off stays suspended
)


# --------------------------------------------------------------------------
# Sender / receiver
# --------------------------------------------------------------------------
class Flow:
    def __init__(self, fid, algo, sim, start_time=0.0, dla_params=None):
        self.fid = fid
        self.algo = algo
        self.sim = sim
        self.start_time = start_time
        self.p = dict(DLA_DEFAULT)
        if dla_params:
            self.p.update(dla_params)
        # sender state
        self.cwnd = 1.0
        self.ssthresh = math.inf
        self.snd_una = 0
        self.snd_nxt = 0
        self.high_sent = 0
        self.dupacks = 0
        self.in_fr = False
        self.recover = -1          # high_sent at the last loss event (guard)
        self.srtt = None
        self.rttvar = None
        self.rto = 1.0
        self.backoff = 1
        self.rto_deadline = None
        self.timer_pending = False
        # DLA state
        self.minrtt_q = deque()    # monotonic deque of (t, rtt)
        self.rtt_fast = None
        self.bwe = 0.0             # packets / s
        self.bw_t0 = None
        self.bw_cnt = 0
        self.last_reduction = -1.0
        self.eb_count = 0
        self.eb_mark = 0
        self.partial_seen = False
        self.compete_until = 0.0
        # receiver state
        self.rcv_next = 0
        self.ooo = set()
        # statistics
        self.sent = 0
        self.drops = 0
        self.retx = 0
        self.delivered = 0
        self.deliv_trace = []      # (t, cumulative delivered)
        self.cwnd_trace = []       # (t, cwnd)
        self.rtt_samples = []      # (t, rtt)
        self.timeouts = 0
        self.fast_rtx = 0
        self.loss_events = 0
        self.random_class = 0
        self.rec_open = None       # (t0, target) for loss-recovery latency
        self.rec_lat = []

    # ---------------- helpers ----------------
    @property
    def flight(self):
        return self.snd_nxt - self.snd_una

    def minrtt(self):
        return self.minrtt_q[0][1] if self.minrtt_q else None

    def qdelay_norm(self):
        m = self.minrtt()
        if m is None or self.rtt_fast is None:
            return 0.0
        return max(0.0, (self.rtt_fast - m) / m)

    def log_cwnd(self):
        self.cwnd_trace.append((self.sim.now, self.cwnd))

    # ---------------- transmission ----------------
    def try_send(self):
        now = self.sim.now
        win = int(max(1.0, self.cwnd))
        while self.snd_nxt < self.snd_una + win:
            seq = self.snd_nxt
            if seq < self.high_sent:
                self.retx += 1
            self.transmit(seq)
            self.snd_nxt += 1
            if self.snd_nxt > self.high_sent:
                self.high_sent = self.snd_nxt
        if self.snd_nxt > self.snd_una and self.rto_deadline is None:
            self.arm_timer()

    def transmit(self, seq):
        now = self.sim.now
        self.sent += 1
        arr = self.sim.link.enqueue(now)
        if arr is None:
            self.drops += 1
        else:
            self.sim.schedule(arr, 1, self, seq, now)

    def retransmit(self, seq):
        self.retx += 1
        self.transmit(seq)

    def arm_timer(self):
        self.rto_deadline = self.sim.now + min(MAX_RTO, self.rto * self.backoff)
        if not self.timer_pending:
            self.timer_pending = True
            self.sim.schedule(self.rto_deadline, 3, self, 0, 0.0)

    # ---------------- receiver ----------------
    def on_data(self, seq, ts):
        now = self.sim.now
        if seq == self.rcv_next:
            self.rcv_next += 1
            while self.rcv_next in self.ooo:
                self.ooo.discard(self.rcv_next)
                self.rcv_next += 1
            new = self.rcv_next - self.delivered
            if new > 0:
                self.delivered = self.rcv_next
                self.deliv_trace.append((now, self.delivered))
        elif seq > self.rcv_next:
            self.ooo.add(seq)
        # ACK travels back over the (uncongested) reverse path
        t_ack = now + self.sim.link.rtt(now) / 2000.0 + self.sim.rng.uniform(0.0, self.sim.jitter)
        if t_ack < self.sim.last_ack_arrival.get(self.fid, 0.0):
            t_ack = self.sim.last_ack_arrival[self.fid]
        self.sim.last_ack_arrival[self.fid] = t_ack
        self.sim.schedule(t_ack, 2, self, self.rcv_next, ts)

    # ---------------- RTT estimation ----------------
    def rtt_update(self, r):
        now = self.sim.now
        self.rtt_samples.append((now, r))
        if self.srtt is None:
            self.srtt, self.rttvar = r, r / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - r)
            self.srtt = 0.875 * self.srtt + 0.125 * r
        self.rto = min(MAX_RTO, max(MIN_RTO, self.srtt + 4 * self.rttvar))
        # windowed min-RTT and fast EWMA (used by DLA)
        q = self.minrtt_q
        while q and q[-1][1] >= r:
            q.pop()
        q.append((now, r))
        while q and q[0][0] < now - self.p["minrtt_win"]:
            q.popleft()
        self.rtt_fast = r if self.rtt_fast is None else 0.75 * self.rtt_fast + 0.25 * r

    def bw_update(self, n):
        now = self.sim.now
        if self.bw_t0 is None:
            self.bw_t0, self.bw_cnt = now, 0
        self.bw_cnt += n
        m = self.minrtt() or 0.05
        if now - self.bw_t0 >= m:
            sample = self.bw_cnt / (now - self.bw_t0)
            self.bwe = sample if self.bwe == 0 else 0.75 * self.bwe + 0.25 * sample
            self.bw_t0, self.bw_cnt = now, 0

    # ---------------- loss-event bookkeeping ----------------
    def open_recovery(self):
        self.loss_events += 1
        if self.rec_open is None:
            self.rec_open = (self.sim.now, self.high_sent)

    def check_recovery(self):
        if self.rec_open and self.snd_una >= self.rec_open[1]:
            self.rec_lat.append(self.sim.now - self.rec_open[0])
            self.rec_open = None

    # ---------------- ACK processing ----------------
    def on_ack(self, ack, ts):
        now = self.sim.now
        a = self.algo
        if ack > self.snd_una:
            newly = ack - self.snd_una
            self.rtt_update(now - ts)
            self.bw_update(newly)
            self.snd_una = ack
            if self.snd_nxt < self.snd_una:
                self.snd_nxt = self.snd_una
            self.backoff = 1
            self.check_recovery()
            reset_timer = True
            if self.in_fr:
                if a == "DLA" and self.p["partial_ack"] and ack < self.recover:
                    # partial ACK: retransmit next hole, stay in recovery.
                    # "Impatient" timer (RFC 6582): re-arm only on the first
                    # partial ACK so that very long recoveries fall back to RTO.
                    self.retransmit(self.snd_una)
                    self.cwnd = max(1.0, self.cwnd - newly + 1)
                    reset_timer = not self.partial_seen
                    self.partial_seen = True
                else:
                    if a == "DLA":    # RFC 6582 exit: avoid a burst
                        self.cwnd = min(self.ssthresh, max(1, self.snd_nxt - self.snd_una) + 1)
                    else:
                        self.cwnd = self.ssthresh
                    self.in_fr = False
                    self.dupacks = 0
            else:
                self.dupacks = 0
                self.increase()
            if self.snd_nxt > self.snd_una and (reset_timer or self.rto_deadline is None):
                self.rto_deadline = now + min(MAX_RTO, self.rto * self.backoff)
                if not self.timer_pending:
                    self.arm_timer()
            elif self.snd_nxt <= self.snd_una:
                self.rto_deadline = None
            self.log_cwnd()
        elif ack == self.snd_una and self.snd_nxt > self.snd_una:
            self.dupacks += 1
            self.bw_update(1)
            if self.in_fr:
                self.cwnd += 1.0           # window inflation (Reno-like)
                self.log_cwnd()
            elif self.dupacks == 3:
                self.on_triple_dup()
        self.try_send()

    def increase(self):
        a = self.algo
        if a == "SS":
            self.cwnd += 1.0
        elif a == "AIMD":
            self.cwnd += 1.0 / self.cwnd
        elif a in ("TAHOE", "RENO"):
            if self.cwnd < self.ssthresh:
                self.cwnd += 1.0
            else:
                self.cwnd += 1.0 / self.cwnd
        elif a == "DLA":
            p = self.p
            d = self.qdelay_norm()
            if self.cwnd < self.ssthresh:
                self.cwnd += 1.0
                if p["ss_exit"] and d > p["d_ss"] and self.cwnd >= 4:
                    self.ssthresh = self.cwnd          # leave slow start early
            else:
                alpha = 1.0
                if p["adaptive_ai"]:
                    alpha = 1.0 + (p["alpha_max"] - 1.0) * max(0.0, 1.0 - d / p["d_target"])
                self.cwnd += alpha / self.cwnd
                now = self.sim.now
                # competition guard: if back-offs do not drain the queue, a
                # loss-based competitor is present -> stop yielding for a while
                if d < p["d_target"]:
                    self.eb_count = 0
                if self.compete_until and now >= self.compete_until:
                    self.compete_until = 0.0
                    self.eb_count = 0
                if (p["early_backoff"] and not self.compete_until and d > p["d_high"]
                        and self.srtt is not None and now_gap(self) > self.srtt
                        and self.snd_una >= self.eb_mark):
                    # (eb_mark: wait until the window sent after the previous
                    #  back-off is acknowledged, so stale RTT samples are ignored)
                    self.eb_count += 1
                    if p["compete_guard"] and self.eb_count > p["eb_max"]:
                        self.compete_until = now + p["compete_hold"]
                        self.cwnd = min(self.cwnd, MAX_CWND)
                        return
                    # drain the standing queue: never keep more than ~0.9 x BDP_est
                    bdp_est = self.bwe * self.minrtt() if self.minrtt() else self.cwnd
                    self.cwnd = max(2.0, min(self.cwnd * p["beta_eb"], 0.9 * bdp_est))
                    self.ssthresh = self.cwnd
                    self.last_reduction = self.sim.now
                    self.eb_mark = self.high_sent
        self.cwnd = min(self.cwnd, MAX_CWND)

    def on_triple_dup(self):
        a = self.algo
        guard = a in ("SS", "TAHOE", "DLA")
        if guard and self.snd_una < self.recover:
            return                              # dup-ACKs from an old episode
        self.open_recovery()
        self.fast_rtx += 1
        self.last_reduction = self.sim.now
        flight = self.flight
        if a == "SS":
            self.recover = self.high_sent
            self.cwnd = 1.0
            self.snd_nxt = self.snd_una         # go-back-N
        elif a == "AIMD":
            self.cwnd = max(1.0, self.cwnd / 2)
            self.retransmit(self.snd_una)
        elif a == "TAHOE":
            self.recover = self.high_sent
            self.ssthresh = max(flight / 2, 2.0)
            self.cwnd = 1.0
            self.snd_nxt = self.snd_una
        elif a == "RENO":
            self.ssthresh = max(flight / 2, 2.0)
            self.retransmit(self.snd_una)
            self.cwnd = self.ssthresh + 3
            self.in_fr = True
        elif a == "DLA":
            p = self.p
            self.recover = self.high_sent
            # base the reduction on the un-inflated window (pipe), as Linux does
            target = max(min(flight, self.cwnd) / 2, 2.0)
            self.partial_seen = False
            d = self.qdelay_norm()
            if p["loss_diff"] and d < p["d_rand"] and self.minrtt():
                # non-congestive loss: back off only to the estimated BDP
                bdp_est = self.bwe * self.minrtt()
                target = max(target, min(p["beta_rand_max"] * self.cwnd, bdp_est))
                self.random_class += 1
            self.ssthresh = target
            self.retransmit(self.snd_una)
            self.cwnd = self.ssthresh + 3
            self.in_fr = True
        self.log_cwnd()

    def on_timeout(self):
        now = self.sim.now
        self.timer_pending = False
        if self.rto_deadline is None:
            return
        if now < self.rto_deadline - 1e-12:
            self.timer_pending = True
            self.sim.schedule(self.rto_deadline, 3, self, 0, 0.0)
            return
        if self.snd_nxt <= self.snd_una:
            self.rto_deadline = None
            return
        # retransmission timeout
        self.timeouts += 1
        self.open_recovery()
        self.last_reduction = now
        flight = self.flight
        if self.algo == "AIMD":
            self.cwnd = max(1.0, self.cwnd / 2)
        else:
            if self.algo != "SS":
                self.ssthresh = max(flight / 2, 2.0)
            self.cwnd = 1.0
        self.in_fr = False
        self.dupacks = 0
        self.recover = self.high_sent
        self.snd_nxt = self.snd_una
        self.backoff = min(self.backoff * 2, 64)
        self.rto_deadline = None
        self.log_cwnd()
        self.try_send()
        if self.rto_deadline is None and self.snd_nxt > self.snd_una:
            self.arm_timer()


def now_gap(f):
    return f.sim.now - f.last_reduction


# --------------------------------------------------------------------------
# Simulator core
# --------------------------------------------------------------------------
class Simulator:
    def __init__(self, cap_mbps=10, rtt_ms=50, loss_p=0.0, buffer_pkts=None,
                 buffer_bdp=1.0, duration=30.0, seed=1, jitter_ms=1.0):
        self.rng = random.Random(seed)
        c0 = Schedule(cap_mbps)(0)
        r0 = Schedule(rtt_ms)(0)
        bdp = c0 * 1e6 * r0 / 1000.0 / MSS_BITS
        if buffer_pkts is None:
            buffer_pkts = max(4, int(round(buffer_bdp * bdp)))
        self.bdp0 = bdp
        self.buffer_pkts = buffer_pkts
        self.link = Bottleneck(cap_mbps, rtt_ms, loss_p, buffer_pkts, self.rng)
        self.duration = duration
        self.now = 0.0
        self.events = []
        self.seq = 0
        self.flows = []
        self.last_ack_arrival = {}
        self.jitter = jitter_ms / 1000.0

    def add_flow(self, algo, start=0.0, dla_params=None):
        f = Flow(len(self.flows), algo, self, start, dla_params)
        self.flows.append(f)
        self.schedule(start, 0, f, 0, 0.0)
        return f

    def schedule(self, t, kind, flow, a, b):
        self.seq += 1
        heapq.heappush(self.events, (t, self.seq, kind, flow, a, b))

    def run(self):
        ev = self.events
        T = self.duration
        while ev:
            t, _, kind, f, a, b = heapq.heappop(ev)
            if t > T:
                break
            self.now = t
            if kind == 1:
                f.on_data(a, b)
            elif kind == 2:
                f.on_ack(a, b)
            elif kind == 3:
                f.on_timeout()
            else:
                f.log_cwnd()
                f.try_send()
        self.now = T
        return self


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def goodput_mbps(f, t0, t1):
    """Goodput (in-order delivered data) of flow f in [t0, t1] in Mbps."""
    def delivered_at(t):
        lo, hi, val = 0, len(f.deliv_trace) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if f.deliv_trace[mid][0] <= t:
                val = f.deliv_trace[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return val
    return (delivered_at(t1) - delivered_at(t0)) * MSS_BITS / (t1 - t0) / 1e6


def throughput_series(f, dt, T):
    times, vals = [], []
    t = 0.0
    while t + dt <= T + 1e-9:
        times.append(t + dt)
        vals.append(goodput_mbps(f, t, t + dt))
        t += dt
    return times, vals


def cwnd_series(f, dt, T):
    out, j, cur = [], 0, 1.0
    tr = f.cwnd_trace
    t = 0.0
    while t <= T + 1e-9:
        while j < len(tr) and tr[j][0] <= t:
            cur = tr[j][1]
            j += 1
        out.append((t, cur))
        t += dt
    return out


def time_avg_cwnd(f, T):
    tr = f.cwnd_trace
    if not tr:
        return 0.0
    acc = 0.0
    for (t0, c), (t1, _) in zip(tr, tr[1:] + [(T, 0)]):
        acc += c * (min(t1, T) - min(t0, T))
    return acc / (T - tr[0][0])


def summarize(sim, f):
    T = sim.duration
    rtts = [r for _, r in f.rtt_samples]
    link = sim.link
    gp = goodput_mbps(f, 0.0, T)
    return dict(
        goodput=gp,
        sent=f.sent,
        retx=f.retx,
        retx_rate=f.retx / max(1, f.sent),
        loss_rate=(f.drops / max(1, f.sent)),
        mean_cwnd=time_avg_cwnd(f, T),
        mean_rtt_ms=1000 * (sum(rtts) / len(rtts)) if rtts else float("nan"),
        p95_rtt_ms=1000 * sorted(rtts)[int(0.95 * (len(rtts) - 1))] if rtts else float("nan"),
        timeouts=f.timeouts,
        fast_rtx=f.fast_rtx,
        loss_events=f.loss_events,
        rec_lat_ms=1000 * (sum(f.rec_lat) / len(f.rec_lat)) if f.rec_lat else 0.0,
        queue_drops=link.queue_drops,
        random_drops=link.random_drops,
    )


def jain(xs):
    s = sum(xs)
    return s * s / (len(xs) * sum(x * x for x in xs)) if s > 0 else 0.0
