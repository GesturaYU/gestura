"""Per-receiver packet rate and sequence-based loss (GES-70).

Implements the packet-metric rules of docs/csi-session-contract-v1.md (contract 1.0.0), Section 2
"Packet metrics" and the `packet_metrics` key of Section 3. Input is the host reader's output
(GES-69): accepted records and `events.ndjson` entries, both carrying `host_monotonic_ns`.

The same `ReceiverMetrics` object works live (fed from the reader callbacks) and offline
(`summarize_capture` replays a capture directory). No I/O except in `summarize_capture`.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

UINT32_RANGE = 2 ** 32
# A drop from near the top of the uint32 range to near zero is a counter wrap, not a sender
# restart. At about 91 ids/s a wrap takes about 1.5 years, so this only guards the arithmetic.
WRAP_MARGIN = 2 ** 20

NFR09_MIN_RATE_HZ = 80.0
NFR09_MAX_LOSS_PERCENT = 5.0  # loss must be below this value

METRIC_KEYS = (
    "accepted_records", "unique_sender_ids", "duplicates", "malformed_lines", "first_id", "last_id",
    "expected_ids", "missing_ids", "sender_restarts", "disconnect_intervals", "rate_interval_s",
    "measured_packet_rate_hz", "estimated_loss_percent", "provisional",
)


def _unsigned(value: int) -> int:
    """Older firmware printed uint32 values with %d; the contract converts v < 0 to v + 2^32."""
    return value + UINT32_RANGE if value < 0 else value


@dataclass
class _Run:
    """A contiguous run of sender ids: no sender restart, wrap or receiver disconnect inside."""
    first_id: int
    last_id: int
    ids: set = field(default_factory=set)

    @property
    def expected(self) -> int:
        return self.last_id - self.first_id + 1


class ReceiverMetrics:
    """Accumulates the packet metrics of one receiver.

    Feed records and events in arrival order. `summary()` may be called at any time, also from
    another thread than the one feeding it.
    """

    def __init__(self, receiver_id: str):
        self.receiver_id = receiver_id
        self._lock = threading.Lock()
        self._runs: List[_Run] = []
        self._accepted = 0
        self._duplicates = 0
        self._malformed = 0
        self._sender_restarts = 0
        self._first_id: Optional[int] = None
        self._last_id: Optional[int] = None
        self._first_ns: Optional[int] = None
        self._last_ns: Optional[int] = None
        self._break_pending = False  # the next record starts a new run (after a disconnect)
        self._disconnects: List[dict] = []
        self._open_disconnect: Optional[dict] = None

    # -- input --------------------------------------------------------------------------------
    def add_record(self, sender_id: int, host_monotonic_ns: Optional[int] = None) -> None:
        """One accepted record (contract Section 2) with its sender `id`."""
        sender_id = _unsigned(int(sender_id))
        with self._lock:
            self._accepted += 1
            if host_monotonic_ns is not None:
                if self._first_ns is None:
                    self._first_ns = host_monotonic_ns
                self._last_ns = host_monotonic_ns
            if self._first_id is None:
                self._first_id = sender_id
            self._last_id = sender_id

            run = self._runs[-1] if self._runs else None
            if run is None:
                self._start_run(sender_id)
            elif self._break_pending:
                # First record after a disconnect: the gap is not ordinary loss. A lower id here
                # means the sender also restarted while the receiver was away.
                if sender_id < run.last_id and not self._is_wrap(run.last_id, sender_id):
                    self._sender_restarts += 1
                self._start_run(sender_id)
            elif sender_id == run.last_id:
                # The same id again. A lower id is not treated as a late repeat even if it was
                # seen before: a restarted sender counts again from 0 or 1 (contract Section 2).
                self._duplicates += 1
            elif sender_id < run.last_id:
                # Lower than the previous id: a new run (contract Section 2). A drop from the top
                # of the range to near zero is a wrap and is not counted as a sender restart.
                if not self._is_wrap(run.last_id, sender_id):
                    self._sender_restarts += 1
                self._start_run(sender_id)
            else:
                run.ids.add(sender_id)
                run.last_id = sender_id
            self._break_pending = False

    def add_event(self, event: dict) -> None:
        """One `events.ndjson` entry of this receiver; kinds that do not affect metrics are ignored."""
        kind = event.get("kind")
        with self._lock:
            if kind == "malformed_record":
                # The line is counted here; its id, if any, stays out of `ids`, so it counts as missing.
                self._malformed += 1
            elif kind == "receiver_disconnected":
                if self._open_disconnect is None:
                    self._open_disconnect = {
                        "disconnected_utc": event.get("host_time_utc"),
                        "reconnected_utc": None,
                        "duration_s": None,
                        "_start_ns": event.get("host_monotonic_ns"),
                    }
                    self._disconnects.append(self._open_disconnect)
                self._break_pending = True
            elif kind == "receiver_reconnected":
                gap = self._open_disconnect
                if gap is not None:
                    gap["reconnected_utc"] = event.get("host_time_utc")
                    end_ns = event.get("host_monotonic_ns")
                    if gap["_start_ns"] is not None and end_ns is not None:
                        gap["duration_s"] = round((end_ns - gap["_start_ns"]) / 1e9, 3)
                    self._open_disconnect = None
                self._break_pending = True

    @staticmethod
    def _is_wrap(previous_id: int, sender_id: int) -> bool:
        return previous_id >= UINT32_RANGE - WRAP_MARGIN and sender_id < WRAP_MARGIN

    def _start_run(self, sender_id: int) -> None:
        self._runs.append(_Run(sender_id, sender_id, {sender_id}))

    # -- output -------------------------------------------------------------------------------
    def capture_span_s(self) -> Optional[float]:
        """Seconds between the first and last accepted record, from the host monotonic clock."""
        with self._lock:
            if self._first_ns is None or self._last_ns is None:
                return None
            return (self._last_ns - self._first_ns) / 1e9

    def summary(self, interval_s: Optional[float] = None, provisional: bool = False) -> dict:
        """The `packet_metrics` entry of this receiver (contract Section 3).

        `interval_s` is the stated interval the rate is measured over. Without it, the span between
        the first and last accepted record is used.
        """
        if interval_s is None:
            interval_s = self.capture_span_s()
        with self._lock:
            unique = sum(len(r.ids) for r in self._runs)
            expected = sum(r.expected for r in self._runs)
            missing = expected - unique
            rate = round(self._accepted / interval_s, 2) if interval_s else None
            loss = round(100.0 * missing / expected, 2) if expected else None
            gaps = [{k: v for k, v in g.items() if not k.startswith("_")} for g in self._disconnects]
            return {
                "accepted_records": self._accepted,
                "unique_sender_ids": unique,
                "duplicates": self._duplicates,
                "malformed_lines": self._malformed,
                "first_id": self._first_id,
                "last_id": self._last_id,
                "expected_ids": expected,
                "missing_ids": missing,
                "sender_restarts": self._sender_restarts,
                "disconnect_intervals": gaps,
                "rate_interval_s": round(interval_s, 3) if interval_s is not None else None,
                "measured_packet_rate_hz": rate,
                "estimated_loss_percent": loss,
                "provisional": provisional,
            }


class SessionMetrics:
    """One `ReceiverMetrics` per receiver; usable as the reader's `on_record` / `on_event`."""

    def __init__(self, receiver_ids: Iterable[str] = ()):
        self.receivers: Dict[str, ReceiverMetrics] = {rid: ReceiverMetrics(rid) for rid in receiver_ids}

    def _get(self, receiver_id: str) -> ReceiverMetrics:
        if receiver_id not in self.receivers:
            self.receivers[receiver_id] = ReceiverMetrics(receiver_id)
        return self.receivers[receiver_id]

    def on_record(self, record: dict) -> None:
        self._get(record["receiver_id"]).add_record(record["id"], record.get("host_monotonic_ns"))

    def on_event(self, event: dict) -> None:
        if "receiver_id" in event:
            self._get(event["receiver_id"]).add_event(event)

    def packet_metrics(self, interval_s: Optional[float] = None, provisional: bool = False) -> dict:
        """The `packet_metrics` value of session.json: receiver_id -> metrics."""
        return {rid: m.summary(interval_s, provisional) for rid, m in sorted(self.receivers.items())}


def _read_ndjson(path: Path) -> List[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize_capture(capture_dir, interval_s: Optional[float] = None,
                      provisional: bool = False) -> dict:
    """Replay a reader output directory (receivers/*.ndjson, events.ndjson) into packet_metrics.

    Records and events are merged per receiver by `host_monotonic_ns`. At equal timestamps records
    come first: the reader stamps a disconnect after the last record it read and a reconnect
    before the next one, so the order is unambiguous for the events that split runs.
    """
    capture_dir = Path(capture_dir)
    rx_files = sorted((capture_dir / "receivers").glob("*.ndjson"))
    if not rx_files:
        raise FileNotFoundError(f"no receivers/*.ndjson in {capture_dir}")
    events_path = capture_dir / "events.ndjson"
    events = _read_ndjson(events_path) if events_path.exists() else []

    session = SessionMetrics(p.stem for p in rx_files)
    for path in rx_files:
        rid = path.stem
        items = [(r.get("host_monotonic_ns", 0), 0, i, r) for i, r in enumerate(_read_ndjson(path))]
        items += [(e.get("host_monotonic_ns", 0), 1, i, e)
                  for i, e in enumerate(events) if e.get("receiver_id") == rid]
        for _, is_event, _, obj in sorted(items, key=lambda t: t[:3]):
            if is_event:
                session.on_event(obj)
            else:
                session.on_record({**obj, "receiver_id": rid})
    return session.packet_metrics(interval_s, provisional)


def compare_nfr09(metrics: dict) -> dict:
    """Compare one receiver's metrics with NFR-09 (at least 80 packets/s, loss below 5 %).

    A value that was not measured is reported as such and never as met.
    """
    rate, loss = metrics.get("measured_packet_rate_hz"), metrics.get("estimated_loss_percent")
    rate_ok = None if rate is None else rate >= NFR09_MIN_RATE_HZ
    loss_ok = None if loss is None else loss < NFR09_MAX_LOSS_PERCENT
    if rate_ok is None or loss_ok is None:
        verdict = "not measured"
    else:
        verdict = "met" if rate_ok and loss_ok else "not met"
    return {"rate_ok": rate_ok, "loss_ok": loss_ok, "verdict": verdict}
