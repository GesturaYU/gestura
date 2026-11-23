import argparse
import csv
import json
import os
import random

import pytest

from gestura.app.collection.alignment import check_alignment
from gestura.app.collection.flow import (AutoUI, FakeClock, FlowConfig, FlowError,
                                         run_signal_check, wait_for_warmup)
from gestura.app.collection.record_session import record
from gestura.app.collection.session_files import TRIALS_COLUMNS
from gestura.app.collection.sources import SyntheticSource
from gestura.app.collection.trial_plan import make_trial_plan


def _args(out):
    return argparse.Namespace(participant="P2", room="R3", room_type="residential",
                              room_role="dataset", other_people=1, recorder_in_room=True, out=str(out))


def _run(tmp_path, failed=None):
    clock = FakeClock()
    beeps = []
    plan = make_trial_plan(random.Random(5))
    return clock, beeps, record(_args(tmp_path), clock, AutoUI(failed=failed),
                                lambda f, ms: beeps.append(f), FlowConfig(), plan=plan)


def test_dry_run_writes_all_trials_and_lines_up(tmp_path):
    _, beeps, (folder, session, trials, log) = _run(tmp_path)
    assert len(trials) == 50
    assert {t.class_label for t in trials} == {"push", "slow_wave", "fast_wave", "no_gesture"}
    assert len(beeps) == 100  # a start and an end beep per trial
    assert check_alignment(trials, log, ["rx1"], 90.0) == []

    with open(os.path.join(folder, "trials.csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == TRIALS_COLUMNS and len(rows) == 51
    assert all(r[0] == session["session_id"] for r in rows[1:])

    with open(os.path.join(folder, "session.json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["signal_check"]["passed"] is True
    assert saved["end_time_utc"] is not None
    assert saved["collection_tool_version"].startswith("dry-run")


def test_every_window_is_three_seconds(tmp_path):
    _, _, (_, _, trials, _) = _run(tmp_path)
    assert all(t.cue_end_monotonic_ns - t.cue_start_monotonic_ns == 3_000_000_000 for t in trials)


def test_failed_trials_are_kept_with_their_reason(tmp_path):
    _, _, (_, _, trials, _) = _run(tmp_path, failed={4: "wrong_gesture"})
    assert [(t.trial_id, t.failed_reason) for t in trials if t.failed] == [(4, "wrong_gesture")]
    assert len(trials) == 50


def test_trials_start_only_after_the_warm_up(tmp_path):
    _, _, (_, _, trials, log) = _run(tmp_path)
    header = next(e.host_monotonic_ns for e in log if e.kind == "header")
    records = [e.host_monotonic_ns for e in log if e.kind == "record"]
    hundredth = sorted(x for x in records if x > header)[99]
    assert trials[0].cue_start_monotonic_ns > hundredth


def test_signal_check_fails_below_80_packets_per_second():
    clock = FakeClock()
    source = SyntheticSource(["rx1"], clock, rate_hz=50.0)
    source.start()
    log = []
    wait_for_warmup(source, clock, FlowConfig(), log)
    result = run_signal_check(source, clock, FlowConfig(), log)
    assert result["passed"] is False
    assert result["receivers"]["rx1"]["packet_rate_hz"] == pytest.approx(50.0, abs=0.2)


def test_a_silent_receiver_stops_the_session_before_any_trial():
    clock = FakeClock()
    source = SyntheticSource(["rx1", "rx2"], clock, silent={"rx2"})
    source.start()
    with pytest.raises(FlowError, match="rx2"):
        wait_for_warmup(source, clock, FlowConfig(warmup_timeout_s=5), [])


def test_alignment_check_reports_a_gap_in_packets():
    clock = FakeClock()
    source = SyntheticSource(["rx1"], clock)
    source.start()
    clock.sleep(5)
    log = source.poll()
    from gestura.app.collection.session_files import TrialRecord
    late = TrialRecord("S1", 1, "push", "", "", 9_000_000_000, 12_000_000_000)
    assert check_alignment([late], log, ["rx1"], 90.0) != []
