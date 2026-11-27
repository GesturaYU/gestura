"""Read one or two receivers concurrently into a session directory (contract Sections 2-3).

Each configured receiver gets its own thread, line buffer and output file, so the streams
cannot mix and a stalled or unplugged receiver never blocks the other one.

Outputs, inside ``session_dir``:

- ``receivers/<receiver_id>.ndjson``: one accepted record per line;
- ``events.ndjson``: malformed records, restarts, configuration mismatches, disconnects and
  reconnects, and stalls (``receiver_stalled`` / ``receiver_resumed``, see README.md).

Typical use from another tool (for example the recording flow, GES-71)::

    session = CaptureSession(session_dir, [ReceiverSpec("rx1", "B2", "5C37270507")])
    session.check_receivers()      # every configured serial number present exactly once
    session.start()
    ...                            # session.status() at any time
    session.stop()
"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import wire
from .ports import Opener, Resolver, Transport, list_usb_serial_ports, open_serial

WARMUP_RECORDS = 100  # contract Section 1: gain is fixed from the 100th packet after boot
READ_CHUNK = 4096


class SessionStartError(RuntimeError):
    """The session cannot start: a receiver is missing, duplicated or cannot be opened."""


@dataclass(frozen=True)
class ReceiverSpec:
    receiver_id: str  # stable id used in file names and session.json, e.g. "rx1"
    board_id: str  # written label on the board, e.g. "B2"
    usb_serial_number: str  # identity of the physical receiver


@dataclass(frozen=True)
class LinkConfig:
    """The session's expected link settings, compared with every accepted record."""
    channel: int = 11
    bandwidth: int = 0  # raw wire value: 0 = 20 MHz
    secondary_channel: int = 0
    stbc: int = 0


@dataclass
class ReceiverStatus:
    receiver_id: str
    board_id: str
    usb_serial_number: str
    state: str = "not_started"  # not_started, connected, stalled, disconnected, stopped
    transport_port: Optional[str] = None
    accepted_records: int = 0
    malformed_lines: int = 0
    last_id: Optional[int] = None
    last_record_host_monotonic_ns: Optional[int] = None
    warmup_records_remaining: Optional[int] = None  # None: gain state unknown (no header seen)


class HostClock:
    """Receipt timestamps. Records and trial cues must use the same monotonic clock."""

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ndjson_line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def _open_new(path: Path):
    # Exclusive creation: never append to or overwrite an earlier capture. "\n" on every OS.
    return open(path, "x", encoding="utf-8", newline="\n", buffering=1)


class _EventLog:
    def __init__(self, path: Path, on_event: Optional[Callable[[dict], None]]):
        self._file = _open_new(path)
        self._lock = threading.Lock()
        self._on_event = on_event

    def write(self, event: dict) -> None:
        with self._lock:
            self._file.write(_ndjson_line(event))
        _notify(self._on_event, event)

    def close(self) -> None:
        with self._lock:
            self._file.close()


def _notify(callback: Optional[Callable[[dict], None]], obj: dict) -> None:
    if callback is None:
        return
    try:
        callback(obj)
    except Exception:  # a consumer bug must not stop the capture, but must not be silent either
        traceback.print_exc(file=sys.stderr)


class _ReceiverWorker(threading.Thread):
    def __init__(self, session: "CaptureSession", spec: ReceiverSpec, transport: Transport,
                 port: str):
        super().__init__(name=f"reader-{spec.receiver_id}", daemon=True)
        self.spec = spec
        self._session = session
        self._transport: Optional[Transport] = transport
        self._buffer = b""
        self._capture_started = False  # a CSI_DATA line has been seen in this session
        self._last_csi_ns = session.clock.monotonic_ns()
        self._stalled_since_ns: Optional[int] = None
        self._logged_mismatch: Optional[tuple] = None
        self._lock = threading.Lock()
        self._status = ReceiverStatus(spec.receiver_id, spec.board_id, spec.usb_serial_number,
                                      state="connected", transport_port=port)
        self._file = _open_new(session.receivers_dir / f"{spec.receiver_id}.ndjson")

    # -- status ---------------------------------------------------------------------------
    def status(self) -> ReceiverStatus:
        with self._lock:
            return ReceiverStatus(**asdict(self._status))

    def _set(self, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._status, key, value)

    # -- main loop ------------------------------------------------------------------------
    def run(self) -> None:
        stop = self._session.stop_event
        try:
            while not stop.is_set():
                if self._transport is None:
                    self._try_reconnect()
                    continue
                try:
                    chunk = self._transport.read(READ_CHUNK)
                except OSError as exc:
                    self._handle_disconnect(str(exc))
                    continue
                if chunk:
                    self._feed(chunk)
                self._check_stall()
        finally:
            if self._buffer:  # a final line without "\n" is still reported, never dropped
                self._process_lines([self._buffer])
                self._buffer = b""
            if self._transport is not None:
                self._transport.close()
            self._set(state="stopped")
            self._file.close()

    def _feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        *complete, self._buffer = self._buffer.split(b"\n")
        if complete:
            self._process_lines(complete)

    def _process_lines(self, raw_lines: Sequence[bytes]) -> None:
        clock = self._session.clock
        now_ns, now_utc = clock.monotonic_ns(), clock.utc_now()
        for raw in raw_lines:
            line = wire.strip_line_ending(raw.decode("utf-8", errors="replace"))
            result = wire.classify_line(line)
            if isinstance(result, wire.Ignored):
                if result.kind == "header":
                    self._on_header(now_ns, now_utc)
                continue
            self._capture_started = True
            self._on_csi_line(now_ns, now_utc)
            if isinstance(result, wire.WireRecord):
                self._on_record(result.fields, line, now_ns, now_utc)
            else:
                self._event(now_ns, now_utc, "malformed_record", reason=result.reason,
                            raw_line=line, id_if_parseable=result.id_if_parseable)
                with self._lock:
                    self._status.malformed_lines += 1

    def _on_header(self, now_ns: int, now_utc: str) -> None:
        if self._capture_started:
            self._event(now_ns, now_utc, "receiver_restarted",
                        warmup_records=WARMUP_RECORDS, transport_port=self._status.transport_port)
        self._set(warmup_records_remaining=WARMUP_RECORDS)

    def _on_record(self, fields: dict, line: str, now_ns: int, now_utc: str) -> None:
        record = {"schema_version": wire.SCHEMA_VERSION, "receiver_id": self.spec.receiver_id,
                  "host_time_utc": now_utc, "host_monotonic_ns": now_ns, **fields,
                  "raw_line": line}
        self._file.write(_ndjson_line(record))
        with self._lock:
            s = self._status
            s.accepted_records += 1
            s.last_id = fields["id"]
            s.last_record_host_monotonic_ns = now_ns
            if s.warmup_records_remaining:
                s.warmup_records_remaining -= 1
        self._check_config(fields, now_ns, now_utc)
        _notify(self._session.on_record, record)

    def _check_config(self, fields: dict, now_ns: int, now_utc: str) -> None:
        link = asdict(self._session.link)
        observed = tuple(fields[name] for name in link)
        if observed == tuple(link.values()):
            self._logged_mismatch = None
            return
        if observed == self._logged_mismatch:
            return  # logged when the value changes, not for every record
        self._logged_mismatch = observed
        mismatches = {name: {"expected": expected, "actual": fields[name]}
                      for name, expected in link.items() if fields[name] != expected}
        self._event(now_ns, now_utc, "config_mismatch", id=fields["id"], mismatches=mismatches)

    # -- stalls, disconnects and reconnects -----------------------------------------------
    def _on_csi_line(self, now_ns: int, now_utc: str) -> None:
        self._last_csi_ns = now_ns
        if self._stalled_since_ns is not None:
            stalled_s = (now_ns - self._stalled_since_ns) / 1e9
            self._stalled_since_ns = None
            self._set(state="connected")
            self._event(now_ns, now_utc, "receiver_resumed", stalled_for_s=round(stalled_s, 3))

    def _check_stall(self) -> None:
        if self._stalled_since_ns is not None:
            return
        clock = self._session.clock
        now_ns = clock.monotonic_ns()
        silent_s = (now_ns - self._last_csi_ns) / 1e9
        if silent_s >= self._session.stall_timeout_s:
            self._stalled_since_ns = self._last_csi_ns
            self._set(state="stalled")
            self._event(now_ns, clock.utc_now(), "receiver_stalled",
                        seconds_without_records=round(silent_s, 3),
                        transport_port=self._status.transport_port)

    def _handle_disconnect(self, error: str) -> None:
        if self._buffer:  # the line cut by the disconnect is reported, not dropped
            self._process_lines([self._buffer])
            self._buffer = b""
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._stalled_since_ns = None
        clock = self._session.clock
        port = self._status.transport_port
        self._set(state="disconnected")
        self._event(clock.monotonic_ns(), clock.utc_now(), "receiver_disconnected",
                    transport_port=port, error=error)

    def _try_reconnect(self) -> None:
        session = self._session
        port = None
        try:
            found = session.resolver().get(self.spec.usb_serial_number, [])
            if len(found) == 1:  # never reconnect to one of several ports with the same serial
                port = found[0]
                self._transport = session.opener(port)
        except OSError:
            self._transport = None
        if self._transport is None:
            session.stop_event.wait(session.reconnect_poll_s)
            return
        clock = session.clock
        now_ns = clock.monotonic_ns()
        self._last_csi_ns = now_ns
        # Replugging a receiver restarts it. On Windows its header was printed before the port
        # could be reopened (GES-69 board check), so the gain warm-up state is unknown until a
        # header is seen; on macOS the header arrives after the reopen and starts the count.
        self._set(state="connected", transport_port=port, warmup_records_remaining=None)
        self._event(now_ns, clock.utc_now(), "receiver_reconnected", transport_port=port)

    def _event(self, now_ns: int, now_utc: str, kind: str, **extra) -> None:
        self._session.events.write({"host_time_utc": now_utc, "host_monotonic_ns": now_ns,
                                    "receiver_id": self.spec.receiver_id, "kind": kind, **extra})


class CaptureSession:
    def __init__(self, session_dir, receivers: Sequence[ReceiverSpec],
                 link: LinkConfig = LinkConfig(), *,
                 resolver: Resolver = list_usb_serial_ports, opener: Opener = open_serial,
                 clock: Optional[HostClock] = None, stall_timeout_s: float = 2.0,
                 reconnect_poll_s: float = 0.5,
                 on_record: Optional[Callable[[dict], None]] = None,
                 on_event: Optional[Callable[[dict], None]] = None):
        if not 1 <= len(receivers) <= 2:
            raise ValueError("configure one or two receivers")
        for attr in ("receiver_id", "board_id", "usb_serial_number"):
            values = [getattr(r, attr) for r in receivers]
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {attr} in receiver configuration: {values}")
        self.session_dir = Path(session_dir)
        self.receivers_dir = self.session_dir / "receivers"
        self.receivers = list(receivers)
        self.link = link
        self.resolver = resolver
        self.opener = opener
        self.clock = clock or HostClock()
        self.stall_timeout_s = stall_timeout_s
        self.reconnect_poll_s = reconnect_poll_s
        self.on_record = on_record
        self.on_event = on_event
        self.stop_event = threading.Event()
        self.events: Optional[_EventLog] = None
        self._workers: List[_ReceiverWorker] = []
        self._start_ports: Dict[str, str] = {}

    def check_receivers(self) -> Dict[str, str]:
        """Check that every configured serial number is present exactly once (contract Section 2).

        Return receiver_id -> port. Raise SessionStartError if a serial number is missing or is
        reported by more than one port; the reader never picks one of several ports.
        """
        ports = self.resolver()
        problems = []
        for r in self.receivers:
            found = ports.get(r.usb_serial_number, [])
            if not found:
                problems.append(f"receiver not connected: {r.receiver_id} ({r.board_id}, "
                                f"serial {r.usb_serial_number})")
            elif len(found) > 1:
                problems.append(f"serial {r.usb_serial_number} of {r.receiver_id} ({r.board_id}) "
                                f"is reported by several ports: {', '.join(sorted(found))}")
        if problems:
            raise SessionStartError("; ".join(problems))
        return {r.receiver_id: ports[r.usb_serial_number][0] for r in self.receivers}

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("session already started")
        ports = self.check_receivers()
        transports: Dict[str, Transport] = {}
        try:
            for spec in self.receivers:
                try:
                    transports[spec.receiver_id] = self.opener(ports[spec.receiver_id])
                except OSError as exc:
                    raise SessionStartError(
                        f"cannot open {ports[spec.receiver_id]} for {spec.receiver_id}: {exc}"
                    ) from exc
            self.receivers_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.events = _EventLog(self.session_dir / "events.ndjson", self.on_event)
                for spec in self.receivers:
                    self._workers.append(_ReceiverWorker(
                        self, spec, transports[spec.receiver_id], ports[spec.receiver_id]))
            except FileExistsError as exc:
                raise SessionStartError(f"session files already exist: {exc.filename}") from exc
        except BaseException:
            for transport in transports.values():
                transport.close()
            for worker in self._workers:
                worker._file.close()
            self._workers = []
            if self.events is not None:
                self.events.close()
                self.events = None
            raise
        self._start_ports = ports
        for worker in self._workers:
            worker.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self.stop_event.set()
        for worker in self._workers:
            worker.join(timeout_s)
        if self.events is not None:
            self.events.close()

    def status(self) -> List[ReceiverStatus]:
        """Current state of every configured receiver, in configuration order."""
        started = {w.spec.receiver_id: w.status() for w in self._workers}
        return [started.get(r.receiver_id) or
                ReceiverStatus(r.receiver_id, r.board_id, r.usb_serial_number)
                for r in self.receivers]

    def receivers_info(self) -> List[dict]:
        """The reader's part of each session.json "receivers" entry."""
        return [{"receiver_id": r.receiver_id, "board_id": r.board_id,
                 "usb_serial_number": r.usb_serial_number,
                 "transport_port": self._start_ports.get(r.receiver_id)}
                for r in self.receivers]

    def __enter__(self) -> "CaptureSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
