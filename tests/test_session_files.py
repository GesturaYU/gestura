import csv
import json
import os
from datetime import datetime, timezone

import pytest

from gestura.app.collection.session_files import (
    SESSION_KEYS, TRIALS_COLUMNS, TrialRecord, make_session_id, new_session,
    recording_date, session_dir, to_rfc3339, write_session_json, write_trials_csv,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ges67_session_v1.json")

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


def _session(**kw):
    args = dict(start_utc=datetime(2026, 11, 21, 9, 0, 0, tzinfo=timezone.utc),
                participant_code="P1", room_id="R1", room_type="campus", room_role="dataset",
                setup=SETUP, other_people_count=1, recorder_in_room=True,
                collection_tool_version="test")
    args.update(kw)
    return new_session(**args)


def test_session_id_follows_the_contract_example():
    assert make_session_id(datetime(2026, 11, 21, 9, 0, 0, tzinfo=timezone.utc)) == "S20261121-090000"


def test_recording_date_is_the_local_istanbul_date():
    # 22:30 UTC is 01:30 the next day in Istanbul.
    assert recording_date(datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc)) == "2026-11-22"


def test_rfc3339_has_milliseconds_and_z():
    dt = datetime(2026, 11, 21, 9, 0, 0, 250999, tzinfo=timezone.utc)
    assert to_rfc3339(dt) == "2026-11-21T09:00:00.250Z"


def test_session_has_the_same_keys_as_the_ges67_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        fixture = json.load(f)
    session = _session()
    assert list(session) == SESSION_KEYS
    assert set(session) == set(fixture)
    assert set(session["packet_metrics"]["rx1"]) == set(fixture["packet_metrics"]["rx1"])


def test_session_folder_name(tmp_path):
    path = session_dir(str(tmp_path), _session())
    assert path.endswith(os.path.join("sessions", "2026-11-21_S20261121-090000"))


@pytest.mark.parametrize("kw", [{"participant_code": "P6"}, {"room_id": "kitchen"},
                                {"room_role": "test"}, {"other_people_count": 0}])
def test_invalid_metadata_is_rejected(kw):
    with pytest.raises(ValueError):
        _session(**kw)


def test_session_json_round_trip(tmp_path):
    path = str(tmp_path / "session.json")
    write_session_json(path, _session())
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["session_id"] == "S20261121-090000"
    assert not os.path.exists(path + ".tmp")


def _trial(**kw):
    args = dict(session_id="S20261121-090000", trial_id=1, class_label="push",
                cue_start_utc="2026-11-21T09:00:10.000Z", cue_end_utc="2026-11-21T09:00:13.000Z",
                cue_start_monotonic_ns=10_000_000_000, cue_end_monotonic_ns=13_000_000_000)
    args.update(kw)
    return TrialRecord(**args)


def test_trials_csv_has_the_contract_columns_in_order(tmp_path):
    path = str(tmp_path / "trials.csv")
    write_trials_csv(path, [_trial(), _trial(trial_id=2, class_label="no_gesture", failed=True,
                                            failed_reason="cue_missed")])
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == TRIALS_COLUMNS
    assert rows[1][7:] == ["false", ""]
    assert rows[2][7:] == ["true", "cue_missed"]


@pytest.mark.parametrize("kw", [{"class_label": "wave"},
                                {"cue_end_monotonic_ns": 10_000_000_000},
                                {"failed": True, "failed_reason": ""},
                                {"failed_reason": "cue_missed"}])
def test_invalid_trials_are_rejected(kw):
    with pytest.raises(ValueError):
        _trial(**kw).as_row()
