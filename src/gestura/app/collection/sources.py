"""Where the recording flow gets receiver activity from.

The flow only needs to know when each receiver restarted (header row) and when its records
arrived on the host monotonic clock. The host reader (GES-69) will provide this from real
receivers; until it is merged, SyntheticSource produces the same information without boards.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceEvent:
    receiver_id: str
    host_monotonic_ns: int
    kind: str  # "header" (receiver restarted) or "record" (an accepted CSI record)


class SyntheticSource:
    """Fake receivers: a header row shortly after start, then records at a steady rate.

    Events are computed from the clock when poll() is called, so no thread is needed and
    tests with a fake clock are exact. Output is test data, never a participant recording.
    """

    def __init__(self, receiver_ids, clock, rate_hz=90.0, header_delay_s=0.35, silent=()):
        self.receiver_ids = list(receiver_ids)
        self.clock = clock
        self.period_ns = int(1e9 / rate_hz)
        self.header_delay_ns = int(header_delay_s * 1e9)
        self.silent = set(silent)  # receivers that never send, to test a missing receiver
        self._start_ns = None
        self._next = {}

    def start(self):
        self._start_ns = self.clock.monotonic_ns()
        header_ns = self._start_ns + self.header_delay_ns
        for rid in self.receiver_ids:
            self._next[rid] = ("header", header_ns)

    def poll(self):
        """Return the events that happened since the previous poll, in time order."""
        now = self.clock.monotonic_ns()
        out = []
        for rid in self.receiver_ids:
            if rid in self.silent:
                continue
            kind, t = self._next[rid]
            while t <= now:
                out.append(SourceEvent(rid, t, kind))
                kind = "record"
                t += self.period_ns
            self._next[rid] = (kind, t)
        out.sort(key=lambda e: e.host_monotonic_ns)
        return out

    def stop(self):
        pass
