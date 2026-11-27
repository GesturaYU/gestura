"""Validate recorded sessions against the CSI session contract (GES-72).

Checks one session directory, or every session of a dataset root, against
docs/csi-session-contract-v1.md (schema 1.0.0), Sections 2 and 3: the directory layout,
session.json, trials.csv, receivers/<receiver_id>.ndjson and events.ndjson. Each finding names
the file, the line or field, and what is wrong.

    python -m gestura.dataset.validate data/dry_runs
    python -m gestura.dataset.validate <dataset root>/sessions/2026-11-21_S20261121-090000

Exit code 0: no errors. 1: at least one error. 2: the path is not a session or a dataset.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gestura.acquisition.reader import wire
from gestura.app.collection.flow import FlowConfig
from gestura.app.collection.session_files import (
    CLASS_LABELS, FAILED_REASONS, PACKET_METRIC_KEYS, SCHEMA_VERSION, SESSION_KEYS,
    TRIALS_COLUMNS, make_session_id, recording_date,
)
from gestura.app.collection.trial_plan import CLASS_COUNTS

ERROR, WARNING, INFO = "error", "warning", "info"

ROOM_TYPES = ("residential", "campus")
ROOM_ROLES = ("dataset", "final_demo")
SESSION_TYPES = ("cued_trials", "continuous_no_gesture")
SENDER_KEYS = ("board_id", "firmware_commit", "channel", "bandwidth_mhz", "configured_packet_rate_hz")
RECEIVER_KEYS = ("receiver_id", "board_id", "usb_serial_number", "transport_port", "firmware_commit",
                 "channel", "bandwidth_mhz", "gain_setting")
PLACEMENT_RECEIVER_KEYS = ("distance_from_sender_m", "height_m", "antenna_orientation")
SIGNAL_CHECK_KEYS = ("time_utc", "interval_s", "passed", "receivers")
RECORD_KEYS = ("schema_version", "receiver_id", "host_time_utc", "host_monotonic_ns") + wire.FIELDS + ("raw_line",)
EVENT_KEYS = ("host_time_utc", "host_monotonic_ns", "receiver_id", "kind")
# Contract Section 2. receiver_stalled / receiver_resumed are written by the GES-69 reader and
# added to the contract's events table by GES-70 (PR #10).
EVENT_FIELDS = {
    "malformed_record": ("reason", "raw_line", "id_if_parseable"),
    "receiver_restarted": (),
    "config_mismatch": ("mismatches",),
    "receiver_disconnected": (),
    "receiver_reconnected": ("transport_port",),
    "receiver_stalled": ("seconds_without_records",),
    "receiver_resumed": ("stalled_for_s",),
}
REASON_CODES = (wire.WRONG_FIELD_COUNT, wire.INVALID_INTEGER, wire.INVALID_MAC, wire.INVALID_PAYLOAD,
                wire.INVALID_PAYLOAD_INTEGER, wire.PAYLOAD_VALUE_OUT_OF_RANGE, wire.PAYLOAD_LENGTH_MISMATCH)
BANDWIDTH_FIELD = {20: 0, 40: 1}  # bandwidth_mhz in session.json -> raw wire `bandwidth`

MIN_PACKET_RATE_HZ = FlowConfig().min_packet_rate_hz  # NFR-09
MIN_WINDOW_FRACTION = 0.8  # as in gestura.app.collection.alignment.check_alignment
MAX_FINDINGS_PER_FILE = 20  # per file and level; the rest are summarized
OS_METADATA = {".DS_Store", "Thumbs.db", "desktop.ini"}

_SESSION_ID_RE = re.compile(r"S\d{8}-\d{6}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UTC_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z")
_PARTICIPANT_RE = re.compile(r"P[1-5]")
_ROOM_RE = re.compile(r"R[1-9][0-9]*")
_RECEIVER_ID_RE = re.compile(r"rx[1-9][0-9]*")
_UINT_RE = re.compile(r"[0-9]+")

CONSENT_NOTE = ("consent forms and the private participant and room lists belong in the "
                "'Gestura Signed Consent' folder, never with the recordings (contract Section 4)")


@dataclass(frozen=True)
class Finding:
    level: str
    path: str       # relative to the session directory or dataset root, "" for the directory
    location: str  # e.g. "line 14 (trial 13)"; "" for the whole file
    field: str
    message: str

    def __str__(self) -> str:
        where = " ".join(p for p in (self.path, self.location) if p) or "(directory)"
        name = f", {self.field}" if self.field else ""
        return f"{self.level.upper():<7} {where}{name}: {self.message}"


@dataclass
class SessionReport:
    session_dir: Path
    findings: List[Finding] = field(default_factory=list)
    session_id: Optional[str] = None
    dry_run: bool = False

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)

    @property
    def passed(self) -> bool:
        return self.count(ERROR) == 0


@dataclass
class ValidationResult:
    """Every session checked, plus findings about the dataset root itself."""
    sessions: List[SessionReport] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    def count(self, level: str) -> int:
        return (sum(1 for f in self.findings if f.level == level)
                + sum(s.count(level) for s in self.sessions))

    @property
    def passed(self) -> bool:
        return self.count(ERROR) == 0


@dataclass(frozen=True)
class _Trial:
    trial_id: str
    line: int
    start_ns: int
    end_ns: int
    failed: bool


def parse_utc(value) -> Optional[datetime]:
    """RFC 3339 UTC with a trailing Z and optional fraction (up to ns), else None."""
    if not isinstance(value, str):
        return None
    m = _UTC_RE.fullmatch(value)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    micro = int((m.group(2) or "").ljust(6, "0")[:6])
    return dt.replace(microsecond=micro, tzinfo=timezone.utc)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _ignored(p: Path) -> bool:
    return p.name in OS_METADATA or p.name.startswith("._")


class _Checker:
    """Collects the findings of one session; limits how many are shown per file."""

    def __init__(self, report: SessionReport):
        self.report = report
        self._shown: Counter = Counter()
        self._hidden: Counter = Counter()

    def add(self, level: str, path: str, message: str, field: str = "", location: str = "") -> None:
        key = (path, level)
        if self._shown[key] >= MAX_FINDINGS_PER_FILE:
            self._hidden[key] += 1
            return
        self._shown[key] += 1
        self.report.findings.append(Finding(level, path, location, field, message))

    def finish(self) -> None:
        for (path, level), n in sorted(self._hidden.items()):
            self.report.findings.append(Finding(level, path, "", "", f"{n} more {level}s in this file are not shown"))


# -- session.json ---------------------------------------------------------------------------
def _check_session_json(c: _Checker, s: dict, dry_run: bool) -> List[str]:
    """Check the keys of contract Section 3; return the receiver ids that can be used further."""
    P = "session.json"
    for key in SESSION_KEYS:
        if key not in s:
            c.add(ERROR, P, "required key is missing (contract Section 3)", key)
    for key in s:
        if key not in SESSION_KEYS:
            c.add(WARNING, P, "not a key of contract 1.0.0; do not store names or other identifying "
                              "data in session.json", key)

    if "schema_version" in s and s["schema_version"] != SCHEMA_VERSION:
        c.add(ERROR, P, f"is {s['schema_version']!r}; this validator checks schema {SCHEMA_VERSION}",
              "schema_version")

    start = parse_utc(s.get("start_time_utc"))
    if "start_time_utc" in s and start is None:
        c.add(ERROR, P, f"must be RFC 3339 UTC such as 2026-11-21T09:00:00.000Z, got {s['start_time_utc']!r}",
              "start_time_utc")
    if "end_time_utc" in s:
        end = parse_utc(s["end_time_utc"])
        if s["end_time_utc"] is None:
            c.add(ERROR, P, "is null: the session was not finalized (the recording flow writes it "
                            "when the session ends)", "end_time_utc")
        elif end is None:
            c.add(ERROR, P, f"must be RFC 3339 UTC such as 2026-11-21T09:20:00.000Z, got {s['end_time_utc']!r}",
                  "end_time_utc")
        elif start is not None and end <= start:
            c.add(ERROR, P, "is not after start_time_utc", "end_time_utc")

    sid = s.get("session_id")
    if "session_id" in s:
        if not (isinstance(sid, str) and _SESSION_ID_RE.fullmatch(sid)):
            c.add(ERROR, P, f"must be S<YYYYMMDD>-<HHMMSS> from the UTC start time, got {sid!r}", "session_id")
        elif start is not None and make_session_id(start) != sid:
            c.add(ERROR, P, f"does not match start_time_utc (expected {make_session_id(start)})", "session_id")

    if "recording_date" in s:
        rd = s["recording_date"]
        if not (isinstance(rd, str) and _DATE_RE.fullmatch(rd)):
            c.add(ERROR, P, f"must be YYYY-MM-DD, got {rd!r}", "recording_date")
        elif start is not None and recording_date(start) != rd:
            c.add(ERROR, P, f"is {rd}, but the Europe/Istanbul date of start_time_utc is "
                            f"{recording_date(start)}", "recording_date")

    if "participant_code" in s:
        pc = s["participant_code"]
        if not (isinstance(pc, str) and _PARTICIPANT_RE.fullmatch(pc)):
            # The value is not repeated here: it may be a name.
            c.add(ERROR, P, "must be a participant code P1-P5, never a name (value not shown)",
                  "participant_code")
    if "room_id" in s and not (isinstance(s["room_id"], str) and _ROOM_RE.fullmatch(s["room_id"])):
        c.add(ERROR, P, "must be a room code R1, R2, ... from the private room list, never a "
                        "description (value not shown)", "room_id")
    for key, allowed in (("room_type", ROOM_TYPES), ("room_role", ROOM_ROLES), ("session_type", SESSION_TYPES)):
        if key in s and s[key] not in allowed:
            c.add(ERROR, P, f"is {s[key]!r}, not one of {', '.join(allowed)}", key)

    sender = _check_sender(c, s)
    receiver_ids = _check_receivers(c, s, sender)
    _check_placement(c, s, receiver_ids)
    _check_people(c, s)
    _check_signal_check(c, s, receiver_ids)
    _check_packet_metrics(c, s, receiver_ids, dry_run)

    if "collection_tool_version" in s and not _nonempty_str(s["collection_tool_version"]):
        c.add(ERROR, P, "must name the host reader and collection tool version or commit",
              "collection_tool_version")
    return receiver_ids


def _check_sender(c: _Checker, s: dict) -> Optional[dict]:
    P = "session.json"
    if "sender" not in s:
        return None
    sender = s["sender"]
    if not isinstance(sender, dict):
        c.add(ERROR, P, f"must be an object with {', '.join(SENDER_KEYS)}", "sender")
        return None
    for key in SENDER_KEYS:
        if key not in sender:
            c.add(ERROR, P, "required key is missing", f"sender.{key}")
    for key in ("board_id", "firmware_commit"):
        if key in sender and not _nonempty_str(sender[key]):
            c.add(ERROR, P, "must be a non-empty string", f"sender.{key}")
    if "channel" in sender and not (_is_int(sender["channel"]) and 1 <= sender["channel"] <= 14):
        c.add(ERROR, P, f"must be a Wi-Fi channel 1-14, got {sender['channel']!r}", "sender.channel")
    if "bandwidth_mhz" in sender and sender["bandwidth_mhz"] not in BANDWIDTH_FIELD:
        c.add(ERROR, P, f"must be 20 or 40, got {sender['bandwidth_mhz']!r}", "sender.bandwidth_mhz")
    rate = sender.get("configured_packet_rate_hz")
    if "configured_packet_rate_hz" in sender and not (_is_number(rate) and rate > 0):
        c.add(ERROR, P, f"must be a positive number, got {rate!r}", "sender.configured_packet_rate_hz")
    return sender


def _check_receivers(c: _Checker, s: dict, sender: Optional[dict]) -> List[str]:
    P = "session.json"
    if "receivers" not in s:
        return []
    receivers = s["receivers"]
    if not isinstance(receivers, list) or not 1 <= len(receivers) <= 2:
        c.add(ERROR, P, "must be a list of one or two receivers", "receivers")
        if not isinstance(receivers, list):
            return []
    ids: List[str] = []
    serials = Counter()
    for i, r in enumerate(receivers):
        name = f"receivers[{i}]"
        if not isinstance(r, dict):
            c.add(ERROR, P, "must be an object", name)
            continue
        for key in RECEIVER_KEYS:
            if key not in r:
                c.add(ERROR, P, "required key is missing", f"{name}.{key}")
        rid = r.get("receiver_id")
        if "receiver_id" in r:
            if not (isinstance(rid, str) and _RECEIVER_ID_RE.fullmatch(rid)):
                c.add(ERROR, P, f"must be rx1, rx2, ..., got {rid!r}", f"{name}.receiver_id")
            elif rid in ids:
                c.add(ERROR, P, f"{rid} is listed twice", f"{name}.receiver_id")
            else:
                ids.append(rid)
        for key in ("board_id", "usb_serial_number", "firmware_commit", "gain_setting"):
            if key in r and not _nonempty_str(r[key]):
                c.add(ERROR, P, "must be a non-empty string", f"{name}.{key}")
        if _nonempty_str(r.get("usb_serial_number")):
            serials[r["usb_serial_number"]] += 1
        if "transport_port" in r and not isinstance(r["transport_port"], str):
            c.add(ERROR, P, "must be a string (the port name, recorded but not used as identity)",
                  f"{name}.transport_port")
        if sender:
            for key in ("channel", "bandwidth_mhz"):
                if key in r and key in sender and r[key] != sender[key]:
                    c.add(ERROR, P, f"is {r[key]!r} but sender.{key} is {sender[key]!r}; every board "
                                    "uses the same link settings", f"{name}.{key}")
    for serial, n in serials.items():
        if n > 1:
            c.add(ERROR, P, "the same USB serial number is listed for two receivers; it identifies "
                            "one physical board (contract Section 2)", "receivers.usb_serial_number")
    return ids


def _check_placement(c: _Checker, s: dict, receiver_ids: List[str]) -> None:
    P = "session.json"
    if "placement" not in s:
        return
    pl = s["placement"]
    if not isinstance(pl, dict):
        c.add(ERROR, P, "must be an object with description, sender_height_m and receivers", "placement")
        return
    for key in ("description", "sender_height_m", "receivers"):
        if key not in pl:
            c.add(ERROR, P, "required key is missing", f"placement.{key}")
    if "description" in pl and not _nonempty_str(pl["description"]):
        c.add(ERROR, P, "must be a short coded description (no address)", "placement.description")
    if "sender_height_m" in pl and not (_is_number(pl["sender_height_m"]) and pl["sender_height_m"] >= 0):
        c.add(ERROR, P, "must be a height in metres (0 or more)", "placement.sender_height_m")
    prs = pl.get("receivers")
    if "receivers" in pl and not isinstance(prs, dict):
        c.add(ERROR, P, "must map each receiver_id to its placement", "placement.receivers")
        return
    if not isinstance(prs, dict):
        return
    for rid in receiver_ids:
        if rid not in prs:
            c.add(ERROR, P, "placement of this receiver is missing", f"placement.receivers.{rid}")
    for rid, p in prs.items():
        name = f"placement.receivers.{rid}"
        if rid not in receiver_ids:
            c.add(ERROR, P, f"{rid} is not listed in receivers", name)
            continue
        if not isinstance(p, dict):
            c.add(ERROR, P, f"must be an object with {', '.join(PLACEMENT_RECEIVER_KEYS)}", name)
            continue
        for key in PLACEMENT_RECEIVER_KEYS:
            if key not in p:
                c.add(ERROR, P, "required key is missing", f"{name}.{key}")
        d = p.get("distance_from_sender_m")
        if "distance_from_sender_m" in p and not (_is_number(d) and d > 0):
            c.add(ERROR, P, "must be the antenna distance in metres (more than 0)", f"{name}.distance_from_sender_m")
        h = p.get("height_m")
        if "height_m" in p and not (_is_number(h) and h >= 0):
            c.add(ERROR, P, "must be a height in metres (0 or more)", f"{name}.height_m")
        o = p.get("antenna_orientation")
        if "antenna_orientation" in p and not (o == "facing_sender" or (isinstance(o, str) and o.startswith("other"))):
            c.add(ERROR, P, f"must be facing_sender, or other with a short note, got {o!r}",
                  f"{name}.antenna_orientation")


def _check_people(c: _Checker, s: dict) -> None:
    P = "session.json"
    count = s.get("other_people_count")
    if "other_people_count" in s and not (_is_int(count) and count >= 0):
        c.add(ERROR, P, f"must be a whole number (0 or more), got {count!r}", "other_people_count")
        count = None
    for key in ("other_people_present", "recorder_in_room"):
        if key in s and not isinstance(s[key], bool):
            c.add(ERROR, P, f"must be true or false, got {s[key]!r}", key)
    if count is None:
        return
    if isinstance(s.get("other_people_present"), bool) and s["other_people_present"] != (count > 0):
        c.add(ERROR, P, f"is {str(s['other_people_present']).lower()} but other_people_count is {count}",
              "other_people_present")
    if s.get("recorder_in_room") is True and count < 1:
        c.add(ERROR, P, "the recorder is in the room, so other_people_count must count them (at least 1)",
              "other_people_count")


def _check_signal_check(c: _Checker, s: dict, receiver_ids: List[str]) -> None:
    P = "session.json"
    if "signal_check" not in s:
        return
    sc = s["signal_check"]
    if sc is None:
        c.add(ERROR, P, "is null: no signal check was recorded (the recording flow runs it before "
                        "the first trial)", "signal_check")
        return
    if not isinstance(sc, dict):
        c.add(ERROR, P, f"must be an object with {', '.join(SIGNAL_CHECK_KEYS)}", "signal_check")
        return
    for key in SIGNAL_CHECK_KEYS:
        if key not in sc:
            c.add(ERROR, P, "required key is missing", f"signal_check.{key}")
    if "time_utc" in sc and parse_utc(sc["time_utc"]) is None:
        c.add(ERROR, P, f"must be RFC 3339 UTC, got {sc['time_utc']!r}", "signal_check.time_utc")
    if "interval_s" in sc and not (_is_number(sc["interval_s"]) and sc["interval_s"] > 0):
        c.add(ERROR, P, "must be a positive number of seconds", "signal_check.interval_s")
    if "passed" in sc:
        if not isinstance(sc["passed"], bool):
            c.add(ERROR, P, f"must be true or false, got {sc['passed']!r}", "signal_check.passed")
        elif not sc["passed"]:
            c.add(ERROR, P, "the signal check failed; this session must not be used for training or "
                            "evaluation", "signal_check.passed")
    rs = sc.get("receivers")
    if "receivers" in sc and not isinstance(rs, dict):
        c.add(ERROR, P, "must map each receiver_id to connected and packet_rate_hz", "signal_check.receivers")
        return
    if not isinstance(rs, dict):
        return
    for rid in receiver_ids:
        name = f"signal_check.receivers.{rid}"
        r = rs.get(rid)
        if r is None:
            c.add(ERROR, P, "result for this receiver is missing", name)
            continue
        if not isinstance(r, dict) or not isinstance(r.get("connected"), bool) \
                or not (_is_number(r.get("packet_rate_hz")) and r["packet_rate_hz"] >= 0):
            c.add(ERROR, P, "must have connected (true or false) and packet_rate_hz (0 or more)", name)
    for rid in rs:
        if rid not in receiver_ids:
            c.add(ERROR, P, f"{rid} is not listed in receivers", f"signal_check.receivers.{rid}")


def _check_packet_metrics(c: _Checker, s: dict, receiver_ids: List[str], dry_run: bool) -> None:
    P = "session.json"
    if "packet_metrics" not in s:
        return
    pm = s["packet_metrics"]
    if not isinstance(pm, dict):
        c.add(ERROR, P, "must map each receiver_id to its packet metrics", "packet_metrics")
        return
    for rid in receiver_ids:
        if rid not in pm:
            c.add(ERROR, P, "packet metrics of this receiver are missing", f"packet_metrics.{rid}")
    for rid, m in pm.items():
        name = f"packet_metrics.{rid}"
        if rid not in receiver_ids:
            c.add(ERROR, P, f"{rid} is not listed in receivers", name)
            continue
        if not isinstance(m, dict):
            c.add(ERROR, P, "must be an object", name)
            continue
        for key in PACKET_METRIC_KEYS + ["provisional"]:
            if key not in m:
                c.add(ERROR, P, "required key is missing", f"{name}.{key}")
        prov = m.get("provisional")
        if "provisional" in m and not isinstance(prov, bool):
            c.add(ERROR, P, f"must be true or false, got {prov!r}", f"{name}.provisional")
        elif prov is True:
            c.add(INFO if dry_run else WARNING, P,
                  "packet metrics are provisional (placeholders or values read during capture); "
                  "after capture, compute them with the GES-70 metrics tool (--write-session)",
                  f"{name}.provisional")
        elif prov is False:
            _check_final_metrics(c, m, name)


def _check_final_metrics(c: _Checker, m: dict, name: str) -> None:
    P = "session.json"
    counts = ("accepted_records", "unique_sender_ids", "duplicates", "malformed_lines",
              "expected_ids", "missing_ids", "sender_restarts")
    for key in counts:
        if key in m and not (_is_int(m[key]) and m[key] >= 0):
            c.add(ERROR, P, f"must be a whole number (0 or more) when provisional is false, got {m[key]!r}",
                  f"{name}.{key}")
    for key in ("first_id", "last_id"):
        if key in m and m[key] is not None and not (_is_int(m[key]) and m[key] >= 0):
            c.add(ERROR, P, f"must be a sender id or null, got {m[key]!r}", f"{name}.{key}")
    for key in ("rate_interval_s", "measured_packet_rate_hz", "estimated_loss_percent"):
        if key in m and m[key] is not None and not _is_number(m[key]):
            c.add(ERROR, P, f"must be a number or null, got {m[key]!r}", f"{name}.{key}")
    if "disconnect_intervals" in m and not isinstance(m["disconnect_intervals"], list):
        c.add(ERROR, P, "must be a list", f"{name}.disconnect_intervals")
    if all(_is_int(m.get(k)) for k in ("expected_ids", "missing_ids", "unique_sender_ids")) \
            and m["missing_ids"] != m["expected_ids"] - m["unique_sender_ids"]:
        c.add(ERROR, P, "missing_ids must equal expected_ids - unique_sender_ids (contract Section 2)",
              f"{name}.missing_ids")


# -- layout -----------------------------------------------------------------------------------
def _check_layout(c: _Checker, session_dir: Path, s: dict, receiver_ids: List[str], dry_run: bool) -> None:
    rd, sid = s.get("recording_date"), s.get("session_id")
    if isinstance(rd, str) and isinstance(sid, str) and session_dir.name != f"{rd}_{sid}":
        c.add(ERROR, "", f"directory is named {session_dir.name!r}; contract Section 3 names it {rd}_{sid}",
              "directory name")

    expected_rx = {f"{rid}.ndjson" for rid in receiver_ids}
    for entry in sorted(session_dir.iterdir()):
        if _ignored(entry):
            continue
        if entry.name in ("session.json", "trials.csv", "events.ndjson") and entry.is_file():
            continue
        if entry.name == "receivers" and entry.is_dir():
            for rx in sorted(entry.iterdir()):
                if _ignored(rx) or (rx.name in expected_rx and rx.is_file()):
                    continue
                c.add(ERROR, f"receivers/{rx.name}", "unexpected file: receivers/ holds one "
                      "<receiver_id>.ndjson per receiver listed in session.json")
            continue
        c.add(ERROR, entry.name, "unexpected file in a session directory: only session.json, "
              f"trials.csv, events.ndjson and receivers/<receiver_id>.ndjson belong here; {CONSENT_NOTE}")

    session_type = s.get("session_type")
    has_trials = (session_dir / "trials.csv").is_file()
    if session_type == "cued_trials" and not has_trials:
        c.add(ERROR, "trials.csv", "missing: a cued_trials session has trials.csv (contract Section 3)")
    if session_type == "continuous_no_gesture" and has_trials:
        c.add(ERROR, "trials.csv", "a continuous_no_gesture session has no trials.csv (contract Section 3)")
    for rid in receiver_ids:
        if not (session_dir / "receivers" / f"{rid}.ndjson").is_file():
            _missing_stream(c, f"receivers/{rid}.ndjson", dry_run, "one file per receiver")
    if not (session_dir / "events.ndjson").is_file():
        _missing_stream(c, "events.ndjson", dry_run, "the events of all receivers")


def _missing_stream(c: _Checker, path: str, dry_run: bool, what: str) -> None:
    if dry_run:
        c.add(INFO, path, "not written by a dry run (synthetic receivers); not checked")
    else:
        c.add(ERROR, path, f"missing: the host reader (GES-69) writes {what} (contract Section 2)")


# -- trials.csv -------------------------------------------------------------------------------
def _check_trials(c: _Checker, path: Path, s: dict) -> List[_Trial]:
    P = "trials.csv"
    trials: List[_Trial] = []
    with path.open(encoding="utf-8", newline="") as f:
        rows = csv.reader(f)
        header = next(rows, None)
        if header != TRIALS_COLUMNS:
            c.add(ERROR, P, f"header must be exactly: {','.join(TRIALS_COLUMNS)}", location="line 1")
            return trials
        start = parse_utc(s.get("start_time_utc"))
        end = parse_utc(s.get("end_time_utc"))
        labels = Counter()
        expected_id = 1
        prev_end_ns = None
        n_rows = 0
        for line_no, row in enumerate(rows, start=2):
            if not row:
                c.add(ERROR, P, "empty line", location=f"line {line_no}")
                continue
            n_rows += 1
            if len(row) != len(TRIALS_COLUMNS):
                c.add(ERROR, P, f"has {len(row)} columns, expected {len(TRIALS_COLUMNS)}", location=f"line {line_no}")
                continue
            t = dict(zip(TRIALS_COLUMNS, row))
            loc = f"line {line_no} (trial {t['trial_id']})"
            if t["session_id"] != s.get("session_id"):
                c.add(ERROR, P, f"is {t['session_id']!r}, not the session_id of session.json", "session_id", loc)
            if not _UINT_RE.fullmatch(t["trial_id"]) or int(t["trial_id"]) != expected_id:
                c.add(ERROR, P, f"trial ids must count 1, 2, 3, ... in order; expected {expected_id}",
                      "trial_id", loc)
            expected_id += 1
            if t["class_label"] in CLASS_LABELS:
                labels[t["class_label"]] += 1
            else:
                c.add(ERROR, P, f"is {t['class_label']!r}, not one of {', '.join(CLASS_LABELS)}", "class_label", loc)
            for key in ("cue_start_utc", "cue_end_utc"):
                when = parse_utc(t[key])
                if when is None:
                    c.add(ERROR, P, f"must be RFC 3339 UTC, got {t[key]!r}", key, loc)
                elif start and end and not (start <= when <= end):
                    c.add(WARNING, P, "is outside the session's start and end time (UTC can jump when "
                                      "the clock synchronizes; labels use the monotonic columns)", key, loc)
            ns = {}
            for key in ("cue_start_monotonic_ns", "cue_end_monotonic_ns"):
                if _UINT_RE.fullmatch(t[key]):
                    ns[key] = int(t[key])
                else:
                    c.add(ERROR, P, f"must be a whole number of nanoseconds, got {t[key]!r}", key, loc)
            failed = t["failed"]
            if failed not in ("true", "false"):
                c.add(ERROR, P, f"must be true or false, got {failed!r}", "failed", loc)
            elif failed == "true" and t["failed_reason"] not in FAILED_REASONS:
                c.add(ERROR, P, f"a failed trial needs a failed_reason: one of {', '.join(FAILED_REASONS)}",
                      "failed_reason", loc)
            elif failed == "false" and t["failed_reason"]:
                c.add(ERROR, P, "only a failed trial has a failed_reason", "failed_reason", loc)
            if len(ns) == 2:
                a, b = ns["cue_start_monotonic_ns"], ns["cue_end_monotonic_ns"]
                if b <= a:
                    c.add(ERROR, P, "cue end is not after cue start", "cue_end_monotonic_ns", loc)
                    continue
                if prev_end_ns is not None and a < prev_end_ns:
                    c.add(ERROR, P, "starts before the previous trial ends; cue windows must not overlap",
                          "cue_start_monotonic_ns", loc)
                prev_end_ns = b
                trials.append(_Trial(t["trial_id"], line_no, a, b, failed == "true"))
        if n_rows == 0:
            c.add(ERROR, P, "has no trials")
        elif dict(labels) != CLASS_COUNTS:
            got = ", ".join(f"{k} {labels.get(k, 0)}" for k in CLASS_LABELS)
            plan = ", ".join(f"{k} {CLASS_COUNTS[k]}" for k in CLASS_LABELS)
            c.add(WARNING, P, f"has {got}; the recording procedure plans {plan} (an interrupted "
                              "session keeps only its finished trials)")
    return trials


# -- receivers/<rid>.ndjson ---------------------------------------------------------------------
def _check_receiver_stream(c: _Checker, path: Path, rid: str, receiver: Optional[dict]) -> List[int]:
    """Check every accepted record; return the host_monotonic_ns values in file order."""
    P = f"receivers/{rid}.ndjson"
    times: List[int] = []
    link = {}
    if isinstance(receiver, dict):
        if _is_int(receiver.get("channel")):
            link["channel"] = receiver["channel"]
        if receiver.get("bandwidth_mhz") in BANDWIDTH_FIELD:
            link["bandwidth"] = BANDWIDTH_FIELD[receiver["bandwidth_mhz"]]
    off_link = 0
    prev_ns = None
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            loc = f"line {line_no}"
            if not line.strip():
                c.add(ERROR, P, "empty line", location=loc)
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                c.add(ERROR, P, f"is not valid JSON ({e})", location=loc)
                continue
            if not isinstance(r, dict):
                c.add(ERROR, P, "must be one JSON object per line", location=loc)
                continue
            missing = [k for k in RECORD_KEYS if k not in r]
            if missing:
                c.add(ERROR, P, f"record is missing {', '.join(missing)} (contract Section 2)", location=loc)
                continue
            if r["schema_version"] != SCHEMA_VERSION:
                c.add(ERROR, P, f"is {r['schema_version']!r}, expected {SCHEMA_VERSION}", "schema_version", loc)
            if r["receiver_id"] != rid:
                c.add(ERROR, P, f"is {r['receiver_id']!r} in the file of {rid}", "receiver_id", loc)
            if parse_utc(r["host_time_utc"]) is None:
                c.add(ERROR, P, f"must be RFC 3339 UTC, got {r['host_time_utc']!r}", "host_time_utc", loc)
            ns = r["host_monotonic_ns"]
            if not (_is_int(ns) and ns >= 0):
                c.add(ERROR, P, f"must be a whole number of nanoseconds, got {ns!r}", "host_monotonic_ns", loc)
            else:
                if prev_ns is not None and ns < prev_ns:
                    c.add(ERROR, P, f"goes back in time ({ns} after {prev_ns}); records must be in "
                                    "arrival order", "host_monotonic_ns", loc)
                prev_ns = ns
                times.append(ns)
            parsed = wire.classify_line(r["raw_line"]) if isinstance(r["raw_line"], str) else None
            if not isinstance(parsed, wire.WireRecord):
                why = parsed.reason if isinstance(parsed, wire.Malformed) else "not a CSI_DATA line"
                c.add(ERROR, P, f"is not an accepted CSI record ({why}); malformed lines belong in "
                                "events.ndjson", "raw_line", loc)
            else:
                differ = [k for k in wire.FIELDS if parsed.fields[k] != r[k]]
                if differ:
                    c.add(ERROR, P, f"stored fields differ from raw_line: {', '.join(differ)}", "raw_line", loc)
            if any(r.get(k) != v for k, v in link.items()):
                off_link += 1
    if not times:
        c.add(ERROR, P, "has no accepted records")
    if off_link:
        c.add(WARNING, P, f"{off_link} records have a channel or bandwidth different from session.json "
                          f"for {rid} (see config_mismatch events)")
    return times


# -- events.ndjson ------------------------------------------------------------------------------
def _check_events(c: _Checker, path: Path, receiver_ids: List[str]) -> Dict[str, List[Tuple[int, float]]]:
    """Check every event; return each receiver's disconnect intervals in host_monotonic_ns."""
    P = "events.ndjson"
    gaps: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    open_gap: Dict[str, int] = {}
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            loc = f"line {line_no}"
            if not line.strip():
                c.add(ERROR, P, "empty line", location=loc)
                continue
            try:
                e = json.loads(line)
            except ValueError as err:
                c.add(ERROR, P, f"is not valid JSON ({err})", location=loc)
                continue
            if not isinstance(e, dict):
                c.add(ERROR, P, "must be one JSON object per line", location=loc)
                continue
            missing = [k for k in EVENT_KEYS if k not in e]
            if missing:
                c.add(ERROR, P, f"event is missing {', '.join(missing)} (contract Section 2)", location=loc)
                continue
            kind, rid, ns = e["kind"], e["receiver_id"], e["host_monotonic_ns"]
            if rid not in receiver_ids:
                c.add(ERROR, P, f"{rid!r} is not listed in session.json receivers", "receiver_id", loc)
            if parse_utc(e["host_time_utc"]) is None:
                c.add(ERROR, P, f"must be RFC 3339 UTC, got {e['host_time_utc']!r}", "host_time_utc", loc)
            if not (_is_int(ns) and ns >= 0):
                c.add(ERROR, P, f"must be a whole number of nanoseconds, got {ns!r}", "host_monotonic_ns", loc)
                ns = None
            if kind not in EVENT_FIELDS:
                c.add(ERROR, P, f"unknown kind {kind!r}; the contract lists {', '.join(EVENT_FIELDS)}", "kind", loc)
                continue
            missing = [k for k in EVENT_FIELDS[kind] if k not in e]
            if missing:
                c.add(ERROR, P, f"a {kind} event needs {', '.join(missing)}", location=loc)
            if kind == "malformed_record":
                if "reason" in e and e["reason"] not in REASON_CODES:
                    c.add(ERROR, P, f"is {e['reason']!r}, not a reason code of contract Section 2", "reason", loc)
                idp = e.get("id_if_parseable")
                if idp is not None and not (_is_int(idp) and idp >= 0):
                    c.add(ERROR, P, f"must be a sender id or null, got {idp!r}", "id_if_parseable", loc)
            elif kind == "receiver_disconnected" and ns is not None:
                open_gap.setdefault(rid, ns)
            elif kind == "receiver_reconnected" and ns is not None and rid in open_gap:
                gaps[rid].append((open_gap.pop(rid), ns))
    for rid, start in open_gap.items():
        gaps[rid].append((start, float("inf")))
    return gaps


# -- labels against packets ---------------------------------------------------------------------
def _check_alignment(c: _Checker, trials: List[_Trial], streams: Dict[str, List[int]],
                     gaps: Dict[str, List[Tuple[int, float]]]) -> None:
    """Mirrors gestura.app.collection.alignment.check_alignment, with errors and warnings apart.

    Failed trials are left out: they are excluded from training and evaluation anyway.
    """
    P = "trials.csv"
    for rid, times in streams.items():
        times = sorted(times)
        if not times:
            continue
        first, last = times[0], times[-1]
        for t in trials:
            if t.failed:
                continue
            loc = f"line {t.line} (trial {t.trial_id})"
            if t.start_ns < first or t.end_ns > last:
                c.add(ERROR, P, f"cue window lies outside the {rid} records (host_monotonic_ns {first} to "
                                f"{last}), so its label cannot be matched to packets", "cue window", loc)
                continue
            if any(a < t.end_ns and b > t.start_ns for a, b in gaps.get(rid, [])):
                c.add(WARNING, P, f"overlaps a {rid} disconnect; review the trial or mark it failed",
                      "cue window", loc)
                continue
            n = bisect.bisect_left(times, t.end_ns) - bisect.bisect_left(times, t.start_ns)
            window_s = (t.end_ns - t.start_ns) / 1e9
            if n < MIN_WINDOW_FRACTION * MIN_PACKET_RATE_HZ * window_s:
                c.add(WARNING, P, f"{rid} has {n} records in the {window_s:.1f} s cue window, fewer than "
                                  f"{MIN_WINDOW_FRACTION:.0%} of {MIN_PACKET_RATE_HZ:g} packets/s; review the "
                                  "trial or mark it failed", "cue window", loc)


# -- entry points -------------------------------------------------------------------------------
def validate_session(session_dir) -> SessionReport:
    """Validate one session directory (contract Section 3)."""
    session_dir = Path(session_dir)
    report = SessionReport(session_dir)
    c = _Checker(report)
    path = session_dir / "session.json"
    s = None
    if not path.is_file():
        c.add(ERROR, "session.json", "missing: every session directory has one (contract Section 3)")
    else:
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            c.add(ERROR, "session.json", f"is not valid JSON ({e})")
        if s is not None and not isinstance(s, dict):
            c.add(ERROR, "session.json", "must be one JSON object")
            s = None
    if s is None:
        c.finish()
        return report

    version = s.get("collection_tool_version")
    report.dry_run = isinstance(version, str) and version.startswith("dry-run")
    report.session_id = s.get("session_id") if isinstance(s.get("session_id"), str) else None
    if report.dry_run:
        c.add(INFO, "session.json", "dry run: test data, not a participant recording", "collection_tool_version")

    receiver_ids = _check_session_json(c, s, report.dry_run)
    _check_layout(c, session_dir, s, receiver_ids, report.dry_run)

    trials: List[_Trial] = []
    if s.get("session_type") == "cued_trials" and (session_dir / "trials.csv").is_file():
        trials = _check_trials(c, session_dir / "trials.csv", s)
    receivers = {r.get("receiver_id"): r for r in s.get("receivers") or [] if isinstance(r, dict)}
    streams = {}
    for rid in receiver_ids:
        rx = session_dir / "receivers" / f"{rid}.ndjson"
        if rx.is_file():
            streams[rid] = _check_receiver_stream(c, rx, rid, receivers.get(rid))
    gaps: Dict[str, List[Tuple[int, float]]] = {}
    if (session_dir / "events.ndjson").is_file():
        gaps = _check_events(c, session_dir / "events.ndjson", receiver_ids)
    _check_alignment(c, trials, streams, gaps)
    c.finish()
    return report


def validate_path(path) -> Optional[ValidationResult]:
    """Validate a session directory, a sessions/ folder or a dataset root with sessions/.

    Return None if the path is none of these.
    """
    path = Path(path)
    result = ValidationResult()
    if (path / "session.json").is_file():
        result.sessions.append(validate_session(path))
        return result
    if (path / "sessions").is_dir():
        sessions_dir = path / "sessions"
        for entry in sorted(path.iterdir()):
            if entry.name != "sessions" and not _ignored(entry):
                result.findings.append(Finding(WARNING, entry.name, "", "", "not part of the dataset layout "
                                       f"(contract Section 3: <root>/sessions/<date>_<session_id>/); {CONSENT_NOTE}"))
    elif path.name == "sessions" and path.is_dir():
        sessions_dir = path
    else:
        return None

    for entry in sorted(sessions_dir.iterdir()):
        if _ignored(entry):
            continue
        if not entry.is_dir():
            result.findings.append(Finding(ERROR, f"sessions/{entry.name}", "", "",
                                           f"sessions/ holds only session directories; {CONSENT_NOTE}"))
            continue
        result.sessions.append(validate_session(entry))
    if not result.sessions:
        result.findings.append(Finding(ERROR, "sessions", "", "", "no session directories found"))
    ids = Counter(r.session_id for r in result.sessions if r.session_id)
    for sid, n in sorted(ids.items()):
        if n > 1:
            result.findings.append(Finding(ERROR, "sessions", "", "session_id",
                                           f"{sid} is used by {n} session directories"))
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m gestura.dataset.validate",
                                description="Validate recorded sessions against the CSI session contract (GES-72).")
    p.add_argument("path", type=Path,
                   help="a session directory, a sessions/ folder or a dataset root that contains sessions/")
    p.add_argument("-q", "--quiet", action="store_true", help="show only errors and the summary")
    args = p.parse_args(argv)

    if not args.path.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 2
    result = validate_path(args.path)
    if result is None:
        print(f"error: {args.path} has no session.json and no sessions/ folder", file=sys.stderr)
        return 2

    shown = (ERROR,) if args.quiet else (ERROR, WARNING, INFO)
    for f in result.findings:
        if f.level in shown:
            print(f)
    for report in result.sessions:
        verdict = "PASS" if report.passed else "FAIL"
        kind = " (dry run)" if report.dry_run else ""
        print(f"\n{report.session_dir}{kind}: {verdict}, {_plural(report.count(ERROR), 'error')}, "
              f"{_plural(report.count(WARNING), 'warning')}")
        for f in report.findings:
            if f.level in shown:
                print(f"  {f}")
    print(f"\n{_plural(len(result.sessions), 'session')}: {_plural(result.count(ERROR), 'error')}, "
          f"{_plural(result.count(WARNING), 'warning')} -> {'PASS' if result.passed else 'FAIL'}")
    return 0 if result.passed else 1


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
