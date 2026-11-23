"""The scripted cued-trial recording flow (GES-71).

Order of a session (recording procedure GES-73, Section 5): wait for the gain warm-up of
every receiver, run the signal check, then play the trials. For each trial the tool shows the
class, beeps at the start and the end of the cue window and stores both times on the host
monotonic clock (used to match labels to packets) and in UTC (for people). Right after each
trial the recorder can mark it as failed.
"""
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from gestura.app.collection.session_files import FAILED_REASONS, TrialRecord, to_rfc3339


@dataclass
class FlowConfig:
    trial_s: float = 3.0            # cue window; slow wave lasts 2-3 s (GES-73 Section 3)
    lead_in_s: float = 2.0          # time to read the next class before the start beep
    rest_s: float = 2.0             # pause after a trial, before the next class is shown
    warmup_records: int = 100       # records after a header before the gain is fixed (GES-67)
    warmup_timeout_s: float = 30.0
    signal_check_s: float = 10.0
    min_packet_rate_hz: float = 80.0  # NFR-09; loss < 5 % is checked once GES-70 is merged


class RealClock:
    def monotonic_ns(self):
        return time.monotonic_ns()

    def utc_now(self):
        return datetime.now(timezone.utc)

    def sleep(self, seconds):
        time.sleep(seconds)


class FakeClock:
    """A clock for tests and fast dry runs: sleep() advances time instantly."""

    def __init__(self, start_utc=None):
        self._ns = 1_000_000_000
        self._utc0 = start_utc or datetime(2026, 11, 24, 9, 0, 0, tzinfo=timezone.utc)

    def monotonic_ns(self):
        return self._ns

    def utc_now(self):
        return self._utc0 + timedelta(microseconds=(self._ns - 1_000_000_000) // 1000)

    def sleep(self, seconds):
        self._ns += int(seconds * 1e9)


class FlowError(RuntimeError):
    pass


def _poll_into(source, log):
    events = source.poll()
    log.extend(events)
    return events


def wait_for_warmup(source, clock, cfg, log, poll_s=0.1):
    """Wait until every receiver has sent a header and `warmup_records` records after it."""
    counts = {rid: None for rid in source.receiver_ids}  # None = no header seen yet
    deadline = clock.monotonic_ns() + int(cfg.warmup_timeout_s * 1e9)
    while True:
        for e in _poll_into(source, log):
            if e.kind == "header":
                counts[e.receiver_id] = 0
            elif counts[e.receiver_id] is not None:
                counts[e.receiver_id] += 1
        if all(c is not None and c >= cfg.warmup_records for c in counts.values()):
            return
        if clock.monotonic_ns() >= deadline:
            missing = [rid for rid, c in counts.items() if c is None or c < cfg.warmup_records]
            raise FlowError(f"No gain warm-up from {', '.join(missing)} within {cfg.warmup_timeout_s:g} s. "
                            "Restart the receiver(s) so that the header row is seen.")
        clock.sleep(poll_s)


def run_signal_check(source, clock, cfg, log):
    """Count records per receiver over the check interval and compare with the minimum rate."""
    time_utc = clock.utc_now()
    _poll_into(source, log)
    t0 = clock.monotonic_ns()
    clock.sleep(cfg.signal_check_s)
    t1 = clock.monotonic_ns()
    events = _poll_into(source, log)
    interval_s = (t1 - t0) / 1e9
    receivers = {}
    for rid in source.receiver_ids:
        n = sum(1 for e in events if e.receiver_id == rid and e.kind == "record" and t0 < e.host_monotonic_ns <= t1)
        receivers[rid] = {"connected": n > 0, "packet_rate_hz": round(n / interval_s, 1)}
    passed = all(r["connected"] and r["packet_rate_hz"] >= cfg.min_packet_rate_hz for r in receivers.values())
    return {"time_utc": to_rfc3339(time_utc), "interval_s": cfg.signal_check_s, "passed": passed,
            "receivers": receivers}


def run_trials(plan, session_id, source, clock, ui, beep, cfg, log, on_trial=None):
    """Play the planned trials and return one TrialRecord per trial."""
    done = []
    for i, planned in enumerate(plan, start=1):
        ui.show_next(i, len(plan), planned.class_label)
        clock.sleep(cfg.lead_in_s)

        start_utc, start_ns = clock.utc_now(), clock.monotonic_ns()
        beep(1000, 150)                       # start cue
        clock.sleep(cfg.trial_s - (clock.monotonic_ns() - start_ns) / 1e9)
        end_utc, end_ns = clock.utc_now(), clock.monotonic_ns()
        beep(500, 150)                        # end cue
        _poll_into(source, log)

        failed, reason = ui.ask_failed(planned)
        record = TrialRecord(session_id, planned.trial_id, planned.class_label,
                             to_rfc3339(start_utc), to_rfc3339(end_utc), start_ns, end_ns,
                             failed, reason if failed else "")
        done.append(record)
        if on_trial:
            on_trial(done)
        clock.sleep(cfg.rest_s)
        _poll_into(source, log)  # packets after the end cue show that the window was fully captured
    return done


class ConsoleUI:
    LABELS = {"push": "PUSH", "slow_wave": "SLOW WAVE", "fast_wave": "FAST WAVE",
              "no_gesture": "NO GESTURE"}

    def show(self, text):
        print(text, flush=True)

    def show_next(self, i, n, class_label):
        print(f"\nTrial {i}/{n}:  {self.LABELS[class_label]}   (start beep, then {class_label})", flush=True)

    def ask_failed(self, planned):
        answer = input("  Enter = OK, f = failed: ").strip().lower()
        if answer != "f":
            return False, ""
        for n, reason in enumerate(FAILED_REASONS, start=1):
            print(f"    {n}) {reason}")
        while True:
            choice = input("  Reason: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(FAILED_REASONS):
                return True, FAILED_REASONS[int(choice) - 1]


class AutoUI:
    """Answers without a person: used by tests and by the automatic dry run."""

    def __init__(self, failed=None, quiet=True):
        self.failed = failed or {}  # trial_id -> reason
        self.quiet = quiet

    def show(self, text):
        if not self.quiet:
            print(text)

    def show_next(self, i, n, class_label):
        if not self.quiet:
            print(f"Trial {i}/{n}: {class_label}")

    def ask_failed(self, planned):
        reason = self.failed.get(planned.trial_id)
        return (True, reason) if reason else (False, "")
