"""Session folder, session.json and trials.csv as defined in the CSI session contract.

See docs/csi-session-contract-v1.md, Section 3. This module only writes what the recording
flow (GES-71) owns: the session metadata it knows and the cued trials. Receiver records,
events and packet metrics are written by the host reader (GES-69) and GES-70.
"""
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "1.0.0"

TRIALS_COLUMNS = [
    "session_id", "trial_id", "class_label",
    "cue_start_utc", "cue_end_utc",
    "cue_start_monotonic_ns", "cue_end_monotonic_ns",
    "failed", "failed_reason",
]

SESSION_KEYS = [
    "schema_version", "session_id", "recording_date", "start_time_utc", "end_time_utc",
    "participant_code", "room_id", "room_type", "room_role", "session_type",
    "sender", "receivers", "placement",
    "other_people_present", "other_people_count", "recorder_in_room",
    "signal_check", "packet_metrics", "collection_tool_version",
]

PACKET_METRIC_KEYS = [
    "accepted_records", "unique_sender_ids", "duplicates", "malformed_lines",
    "first_id", "last_id", "expected_ids", "missing_ids", "sender_restarts",
    "disconnect_intervals", "rate_interval_s", "measured_packet_rate_hz",
    "estimated_loss_percent",
]

CLASS_LABELS = ("push", "slow_wave", "fast_wave", "no_gesture")
FAILED_REASONS = ("cue_missed", "hand_not_through_line", "wrong_gesture", "other")


def _local_tz():
    # Windows ships no time zone database; zoneinfo then needs the tzdata package.
    # Turkey has used UTC+3 all year since 2016, so the fixed offset gives the same date.
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Istanbul")
    except Exception:
        return timezone(timedelta(hours=3))


LOCAL_TZ = _local_tz()


def utc_now():
    return datetime.now(timezone.utc)


def to_rfc3339(dt):
    """UTC time as RFC 3339 with milliseconds, e.g. 2026-11-21T09:00:00.250Z."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def make_session_id(start_utc):
    """S<YYYYMMDD>-<HHMMSS> from the UTC start time; no participant or room information."""
    return start_utc.astimezone(timezone.utc).strftime("S%Y%m%d-%H%M%S")


def recording_date(start_utc):
    """Local (Europe/Istanbul) calendar date of the session start, YYYY-MM-DD."""
    return start_utc.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


def session_dir(dataset_root, session):
    return os.path.join(dataset_root, "sessions", f"{session['recording_date']}_{session['session_id']}")


def new_session(start_utc, participant_code, room_id, room_type, room_role, setup,
                other_people_count, recorder_in_room, collection_tool_version,
                session_type="cued_trials"):
    """Build session.json before capture. `setup` holds sender, receivers and placement."""
    if not re.fullmatch(r"P[1-5]", participant_code):
        raise ValueError(f"participant_code must be P1-P5, got {participant_code!r}")
    if not re.fullmatch(r"R[1-9][0-9]*", room_id):
        raise ValueError(f"room_id must look like R1, R2, ..., got {room_id!r}")
    if room_type not in ("residential", "campus"):
        raise ValueError("room_type must be residential or campus")
    if room_role not in ("dataset", "final_demo"):
        raise ValueError("room_role must be dataset or final_demo")
    if other_people_count < 0 or (recorder_in_room and other_people_count < 1):
        raise ValueError("other_people_count must count the recorder when the recorder is in the room")
    receiver_ids = [r["receiver_id"] for r in setup["receivers"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": make_session_id(start_utc),
        "recording_date": recording_date(start_utc),
        "start_time_utc": to_rfc3339(start_utc),
        "end_time_utc": None,
        "participant_code": participant_code,
        "room_id": room_id,
        "room_type": room_type,
        "room_role": room_role,
        "session_type": session_type,
        "sender": setup["sender"],
        "receivers": setup["receivers"],
        "placement": setup["placement"],
        "other_people_present": other_people_count > 0,
        "other_people_count": other_people_count,
        "recorder_in_room": recorder_in_room,
        "signal_check": None,
        # Filled by GES-70; the keys are present so that the file has the contract's shape.
        "packet_metrics": {rid: {**{k: None for k in PACKET_METRIC_KEYS}, "provisional": True}
                           for rid in receiver_ids},
        "collection_tool_version": collection_tool_version,
    }


def write_session_json(path, session):
    """Write session.json; written first to a temporary file so a crash leaves no half file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


@dataclass
class TrialRecord:
    session_id: str
    trial_id: int
    class_label: str
    cue_start_utc: str
    cue_end_utc: str
    cue_start_monotonic_ns: int
    cue_end_monotonic_ns: int
    failed: bool = False
    failed_reason: str = ""

    def as_row(self):
        if self.class_label not in CLASS_LABELS:
            raise ValueError(f"unknown class label {self.class_label!r}")
        if self.cue_end_monotonic_ns <= self.cue_start_monotonic_ns:
            raise ValueError(f"trial {self.trial_id}: cue end is not after cue start")
        if self.failed and self.failed_reason not in FAILED_REASONS:
            raise ValueError(f"trial {self.trial_id}: failed_reason must be one of {FAILED_REASONS}")
        if not self.failed and self.failed_reason:
            raise ValueError(f"trial {self.trial_id}: failed_reason given for a trial that did not fail")
        return [
            self.session_id, self.trial_id, self.class_label,
            self.cue_start_utc, self.cue_end_utc,
            self.cue_start_monotonic_ns, self.cue_end_monotonic_ns,
            "true" if self.failed else "false", self.failed_reason,
        ]


def write_trials_csv(path, trials):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TRIALS_COLUMNS)
        for t in trials:
            w.writerow(t.as_row())
