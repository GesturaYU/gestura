"""GES-72: session validator against the CSI session contract (docs/csi-session-contract-v1.md)."""
import csv
import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gestura.acquisition.reader import wire
from gestura.acquisition.reader.fake_receiver import synthetic_line
from gestura.app.collection import record_session
from gestura.app.collection.session_files import (
    TrialRecord, new_session, session_dir, to_rfc3339, write_session_json, write_trials_csv,
)
from gestura.app.collection.trial_plan import make_trial_plan
from gestura.dataset.validate import ERROR, INFO, WARNING, main, validate_path, validate_session

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
START = datetime(2026, 11, 21, 9, 0, 0, tzinfo=timezone.utc)
T0_NS = 5_000_000_000   # host monotonic clock at the session start
RATE_HZ = 90
TRIAL_S, REST_S, LEAD_S = 0.5, 0.1, 2.0  # short windows keep the synthetic stream small

SETUP = {
    "sender": {"board_id": "B1", "firmware_commit": "synthetic", "channel": 11,
               "bandwidth_mhz": 20, "configured_packet_rate_hz": 100},
    "receivers": [{"receiver_id": "rx1", "board_id": "B2", "usb_serial_number": "SYNTHETIC-B2",
                   "transport_port": "COM10", "firmware_commit": "synthetic", "channel": 11,
                   "bandwidth_mhz": 20, "gain_setting": "forced_after_100_packets"}],
    "placement": {"description": "synthetic table setup", "sender_height_m": 0.75,
                  "receivers": {"rx1": {"distance_from_sender_m": 1.0, "height_m": 0.75,
                                        "antenna_orientation": "facing_sender"}}},
}


def _utc(ns):
    return to_rfc3339(START + timedelta(seconds=(ns - T0_NS) / 1e9))


def _ndjson(path, objs):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for o in objs:
            f.write(json.dumps(o, separators=(",", ":")) + "\n")


def build_session(root: Path) -> Path:
    """A complete synthetic cued session as the reader (GES-69) and the flow (GES-71) write it."""
    session = new_session(START, "P1", "R1", "campus", "dataset", SETUP, other_people_count=1,
                          recorder_in_room=True, collection_tool_version="test 0000000")
    trials, t = [], T0_NS + int(LEAD_S * 1e9)
    for p in make_trial_plan(rng=random.Random(1)):
        a, b = t, t + int(TRIAL_S * 1e9)
        trials.append(TrialRecord(session["session_id"], p.trial_id, p.class_label, _utc(a), _utc(b), a, b))
        t = b + int(REST_S * 1e9)
    end_ns = t + int(1e9)

    records = []
    rng = random.Random(2)
    n = int((end_ns - T0_NS) / 1e9 * RATE_HZ)
    for i in range(n):
        ns = T0_NS + int(i * 1e9 / RATE_HZ)
        line = synthetic_line(1000 + i, 300_000 + i * 11_111, length=4, rng=rng)
        records.append({"schema_version": "1.0.0", "receiver_id": "rx1", "host_time_utc": _utc(ns),
                        "host_monotonic_ns": ns, **wire.classify_line(line).fields, "raw_line": line})

    cut = next(line for line in (FIXTURES / "ges67_wire_v1.csv").read_text(encoding="utf-8").splitlines()
               if "task_wdt" in line)
    bad = wire.classify_line(cut)
    events = [
        {"host_time_utc": _utc(T0_NS + 4_000_000_000), "host_monotonic_ns": T0_NS + 4_000_000_000,
         "receiver_id": "rx1", "kind": "malformed_record", "reason": bad.reason, "raw_line": cut,
         "id_if_parseable": bad.id_if_parseable},
        {"host_time_utc": _utc(T0_NS + 6_000_000_000), "host_monotonic_ns": T0_NS + 6_000_000_000,
         "receiver_id": "rx1", "kind": "receiver_stalled", "seconds_without_records": 2.0,
         "transport_port": "COM10"},
        {"host_time_utc": _utc(T0_NS + 7_000_000_000), "host_monotonic_ns": T0_NS + 7_000_000_000,
         "receiver_id": "rx1", "kind": "receiver_resumed", "stalled_for_s": 3.0},
    ]

    session["end_time_utc"] = _utc(end_ns)
    session["signal_check"] = {"time_utc": _utc(T0_NS + 1_000_000_000), "interval_s": 10.0, "passed": True,
                               "receivers": {"rx1": {"connected": True, "packet_rate_hz": 90.0}}}
    session["packet_metrics"]["rx1"] = {
        "accepted_records": n, "unique_sender_ids": n, "duplicates": 0, "malformed_lines": 1,
        "first_id": 1000, "last_id": 1000 + n - 1, "expected_ids": n, "missing_ids": 0,
        "sender_restarts": 0, "disconnect_intervals": [], "rate_interval_s": round((end_ns - T0_NS) / 1e9, 3),
        "measured_packet_rate_hz": float(RATE_HZ), "estimated_loss_percent": 0.0, "provisional": False,
    }

    folder = Path(session_dir(str(root), session))
    (folder / "receivers").mkdir(parents=True)
    write_session_json(str(folder / "session.json"), session)
    write_trials_csv(str(folder / "trials.csv"), trials)
    _ndjson(folder / "receivers" / "rx1.ndjson", records)
    _ndjson(folder / "events.ndjson", events)
    return folder


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    return build_session(tmp_path_factory.mktemp("template"))


@pytest.fixture
def session(template, tmp_path):
    """A fresh copy of the valid session, at <tmp>/sessions/<date>_<id>, to change in a test."""
    target = tmp_path / "sessions" / template.name
    shutil.copytree(template, target)
    return target


def edit_json(folder, change):
    path = folder / "session.json"
    s = json.loads(path.read_text(encoding="utf-8"))
    change(s)
    path.write_text(json.dumps(s, indent=2), encoding="utf-8")


def edit_trials(folder, change):
    path = folder / "trials.csv"
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    change(rows)  # rows[0] is the header, rows[1] is trial 1
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def edit_ndjson(path, change):
    objs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    change(objs)
    _ndjson(path, objs)


def errors(report):
    return [f for f in report.findings if f.level == ERROR]


def has_error(report, path, field=""):
    return any(f.path == path and field in f.field for f in errors(report))


# -- sessions that pass --------------------------------------------------------------------------
def test_complete_synthetic_session_passes_without_findings(session):
    report = validate_session(session)
    assert report.passed, [str(f) for f in report.findings]
    assert [f for f in report.findings if f.level in (ERROR, WARNING)] == []


def test_recording_flow_dry_run_passes(tmp_path, capsys):
    code = record_session.main(["--dry-run", "--auto", "--participant", "P2", "--room", "R3",
                                "--room-type", "residential", "--other-people", "0", "--out", str(tmp_path)])
    assert code == 0
    capsys.readouterr()
    result = validate_path(tmp_path)
    assert result.passed, [str(f) for s in result.sessions for f in s.findings]
    [report] = result.sessions
    assert report.dry_run
    infos = {f.path for f in report.findings if f.level == INFO}
    assert {"receivers/rx1.ndjson", "events.ndjson"} <= infos  # a dry run writes no stream files


def test_os_metadata_files_are_ignored(session):
    (session / ".DS_Store").write_bytes(b"\0")
    assert validate_session(session).passed


# -- sessions that fail -------------------------------------------------------------------------
def _shift_start(s):
    s["start_time_utc"] = "2026-11-21T09:00:01.000Z"


def _set_trial(row, column, value):
    def change(rows):
        rows[row][rows[0].index(column)] = value
    return change


def _overlap(rows):
    h = rows[0]
    rows[2][h.index("cue_start_monotonic_ns")] = str(int(rows[1][h.index("cue_end_monotonic_ns")]) - 1)


def _after_stream(rows):
    h = rows[0]
    rows[-1][h.index("cue_end_monotonic_ns")] = str(10 ** 15)


def _bad_raw_line(folder):
    edit_ndjson(folder / "receivers" / "rx1.ndjson", lambda objs: objs[10].update(rssi=-1))


def _unknown_event(folder):
    edit_ndjson(folder / "events.ndjson", lambda objs: objs[0].update(kind="receiver_exploded"))


INVALID = [
    ("missing key", lambda f: edit_json(f, lambda s: s.pop("room_type")), "session.json", "room_type"),
    ("schema version", lambda f: edit_json(f, lambda s: s.update(schema_version="2.0.0")), "session.json", "schema_version"),
    ("session_id vs start time", lambda f: edit_json(f, _shift_start), "session.json", "session_id"),
    ("recording date", lambda f: edit_json(f, lambda s: s.update(recording_date="2026-11-22")), "session.json", "recording_date"),
    ("not finalized", lambda f: edit_json(f, lambda s: s.update(end_time_utc=None)), "session.json", "end_time_utc"),
    ("room as description", lambda f: edit_json(f, lambda s: s.update(room_id="Kitchen")), "session.json", "room_id"),
    ("signal check failed", lambda f: edit_json(f, lambda s: s["signal_check"].update(passed=False)), "session.json", "signal_check.passed"),
    ("people count", lambda f: edit_json(f, lambda s: s.update(other_people_count=0)), "session.json", "other_people"),
    ("metrics arithmetic", lambda f: edit_json(f, lambda s: s["packet_metrics"]["rx1"].update(missing_ids=5)), "session.json", "missing_ids"),
    ("class label", lambda f: edit_trials(f, _set_trial(3, "class_label", "pushh")), "trials.csv", "class_label"),
    ("failed without reason", lambda f: edit_trials(f, _set_trial(4, "failed", "true")), "trials.csv", "failed_reason"),
    ("trial id order", lambda f: edit_trials(f, _set_trial(5, "trial_id", "7")), "trials.csv", "trial_id"),
    ("overlapping trials", lambda f: edit_trials(f, _overlap), "trials.csv", "cue_start_monotonic_ns"),
    ("trial outside the stream", lambda f: edit_trials(f, _after_stream), "trials.csv", "cue window"),
    ("missing receiver file", lambda f: (f / "receivers" / "rx1.ndjson").unlink(), "receivers/rx1.ndjson", ""),
    ("record differs from raw_line", _bad_raw_line, "receivers/rx1.ndjson", "raw_line"),
    ("unknown event kind", _unknown_event, "events.ndjson", "kind"),
    ("consent form in a session", lambda f: (f / "consent.pdf").write_bytes(b"%PDF"), "consent.pdf", ""),
    ("unlisted receiver file", lambda f: (f / "receivers" / "rx3.ndjson").write_text("", encoding="utf-8"), "receivers/rx3.ndjson", ""),
]


@pytest.mark.parametrize("change, path, field", [case[1:] for case in INVALID], ids=[case[0] for case in INVALID])
def test_invalid_session_fails_with_an_actionable_error(session, change, path, field):
    change(session)
    report = validate_session(session)
    assert not report.passed
    assert has_error(report, path, field), [str(f) for f in report.findings]


def test_participant_name_fails_and_is_not_repeated_in_the_output(session):
    edit_json(session, lambda s: s.update(participant_code="Ayse Yilmaz"))
    report = validate_session(session)
    assert has_error(report, "session.json", "participant_code")
    assert all("Ayse" not in str(f) for f in report.findings)


def test_wrong_directory_name_fails(session):
    renamed = session.with_name("2026-11-21_S20261121-090001")
    session.rename(renamed)
    report = validate_session(renamed)
    assert has_error(report, "", "directory name")


def test_continuous_session_with_trials_csv_fails(session):
    edit_json(session, lambda s: s.update(session_type="continuous_no_gesture"))
    assert has_error(validate_session(session), "trials.csv")


# -- warnings: the session still passes ---------------------------------------------------------
def test_interrupted_session_warns_about_the_class_counts(session):
    edit_trials(session, lambda rows: rows.__delitem__(slice(-5, None)))
    report = validate_session(session)
    assert report.passed
    assert any(f.level == WARNING and f.path == "trials.csv" and "plans" in f.message for f in report.findings)


def test_trial_during_a_disconnect_warns(session):
    with open(session / "trials.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    start = int(rows[1][rows[0].index("cue_start_monotonic_ns")])

    def add_gap(objs):
        objs.append({"host_time_utc": _utc(start), "host_monotonic_ns": start, "receiver_id": "rx1",
                     "kind": "receiver_disconnected", "transport_port": "COM10", "error": "test"})
        objs.append({"host_time_utc": _utc(start + 10 ** 9), "host_monotonic_ns": start + 10 ** 9,
                     "receiver_id": "rx1", "kind": "receiver_reconnected", "transport_port": "COM11"})
    edit_ndjson(session / "events.ndjson", add_gap)
    report = validate_session(session)
    assert report.passed
    assert any(f.level == WARNING and "disconnect" in f.message for f in report.findings)


def test_extra_key_warns(session):
    edit_json(session, lambda s: s.update(notes="free text"))
    report = validate_session(session)
    assert report.passed
    assert any(f.level == WARNING and f.field == "notes" for f in report.findings)


def test_provisional_metrics_warn_on_a_real_session(session):
    edit_json(session, lambda s: s["packet_metrics"]["rx1"].update(provisional=True))
    report = validate_session(session)
    assert report.passed
    assert any(f.level == WARNING and f.field == "packet_metrics.rx1.provisional" for f in report.findings)


# -- dataset root ---------------------------------------------------------------------------------
def test_dataset_root_reports_files_outside_sessions(session):
    root = session.parent.parent
    (root / "participants.xlsx").write_bytes(b"x")
    (root / "sessions" / "consent-list.csv").write_text("P1,name", encoding="utf-8")
    result = validate_path(root)
    assert any(f.level == WARNING and f.path == "participants.xlsx" for f in result.findings)
    assert any(f.level == ERROR and f.path == "sessions/consent-list.csv" for f in result.findings)
    assert not result.passed


def test_duplicate_session_id_fails(session):
    copy = session.parent / (session.name + "-copy")
    shutil.copytree(session, copy)
    result = validate_path(session.parent.parent)
    assert any(f.field == "session_id" and "2 session directories" in f.message for f in result.findings)


# -- command line ---------------------------------------------------------------------------------
def test_command_line_exit_codes(session, tmp_path, capsys):
    root = session.parent.parent
    assert main([str(root)]) == 0
    assert "-> PASS" in capsys.readouterr().out
    edit_json(session, lambda s: s.update(schema_version="2.0.0"))
    assert main([str(session)]) == 1
    out = capsys.readouterr().out
    assert "schema_version" in out
    assert "-> FAIL" in out
    assert main([str(tmp_path / "missing")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main([str(empty)]) == 2
