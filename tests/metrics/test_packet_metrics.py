"""GES-70: packet rate and sequence-based loss, per contract Section 2 "Packet metrics"."""
import json
from pathlib import Path

import pytest

from gestura.acquisition.metrics import (METRIC_KEYS, ReceiverMetrics, SessionMetrics, compare_nfr09,
                                         summarize_capture)
from gestura.acquisition.metrics.__main__ import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NS = 1_000_000_000


def feed(ids, rate_hz=90.0, start_ns=0, m=None):
    """Accepted records at a steady rate; returns the metrics object and the next timestamp."""
    m = m or ReceiverMetrics("rx1")
    t = start_ns
    for i in ids:
        m.add_record(i, t)
        t += int(NS / rate_hz)
    return m, t


def test_summary_has_exactly_the_contract_keys():
    m, _ = feed(range(10))
    assert tuple(m.summary(1.0)) == METRIC_KEYS


def test_ges67_fixture_reproduces_the_session_fixture_metrics():
    expected = json.loads((FIXTURES / "ges67_expected_v1.json").read_text(encoding="utf-8"))
    session = json.loads((FIXTURES / "ges67_session_v1.json").read_text(encoding="utf-8"))
    m = SessionMetrics(["rx1"])
    items = [(r["line"], "record", r) for r in expected["records"]]
    items += [(e["line"], "event", e) for e in expected["events"]]
    for _, kind, obj in sorted(items, key=lambda t: t[0]):
        if kind == "record":
            m.on_record({"receiver_id": "rx1", "id": obj["id"]})
        else:
            m.on_event({"receiver_id": "rx1", **obj})
    assert m.packet_metrics(interval_s=5, provisional=True) == session["packet_metrics"]


def test_normal_reception_has_no_loss():
    m, _ = feed(range(900))  # 10 s at 90 packets/s
    s = m.summary(10.0)
    assert (s["accepted_records"], s["expected_ids"], s["missing_ids"]) == (900, 900, 0)
    assert s["measured_packet_rate_hz"] == 90.0
    assert s["estimated_loss_percent"] == 0.0
    assert (s["first_id"], s["last_id"]) == (0, 899)


def test_missing_sequence_numbers_are_counted_as_loss():
    ids = [i for i in range(1000) if not 500 <= i < 520]
    s = feed(ids)[0].summary(10.0)
    assert s["missing_ids"] == 20
    assert s["expected_ids"] == 1000
    assert s["estimated_loss_percent"] == 2.0


def test_duplicates_are_counted_separately_and_not_as_ids():
    s = feed([0, 1, 2, 2, 3, 3, 3, 4])[0].summary(1.0)
    assert s["duplicates"] == 3
    assert s["unique_sender_ids"] == 5
    assert s["missing_ids"] == 0
    assert s["sender_restarts"] == 0


def test_restart_to_an_id_already_seen_is_a_restart_not_a_duplicate():
    # A capture that started at sender boot: ids 1..5, then the sender restarts and counts from 1.
    s = feed([1, 2, 3, 4, 5, 1, 2, 3])[0].summary(1.0)
    assert s["sender_restarts"] == 1
    assert s["duplicates"] == 0
    assert (s["expected_ids"], s["missing_ids"]) == (8, 0)


def test_sender_restart_starts_a_new_run_without_counting_the_jump_as_loss():
    # GES-68 hardware check: after a sender power cycle id went from 33,880 to 1.
    ids = list(range(33_800, 33_881)) + list(range(1, 51))
    s = feed(ids)[0].summary(2.0)
    assert s["sender_restarts"] == 1
    assert s["expected_ids"] == 81 + 50
    assert s["missing_ids"] == 0
    assert (s["first_id"], s["last_id"]) == (33_800, 50)


def test_loss_inside_each_run_still_counts_after_a_restart():
    ids = [0, 1, 3, 4] + [0, 2]
    s = feed(ids)[0].summary(1.0)
    assert s["sender_restarts"] == 1
    assert (s["expected_ids"], s["missing_ids"]) == (5 + 3, 2)


def test_uint32_wrap_is_not_a_sender_restart_or_loss():
    top = 2 ** 32
    s = feed([top - 3, top - 2, top - 1, 0, 1, 2])[0].summary(1.0)
    assert s["sender_restarts"] == 0
    assert s["missing_ids"] == 0


def test_negative_ids_from_older_firmware_are_read_as_unsigned():
    s = feed([-3, -2, -1])[0].summary(1.0)  # %d output of 2^32-3 .. 2^32-1
    assert (s["first_id"], s["last_id"]) == (2 ** 32 - 3, 2 ** 32 - 1)
    assert s["missing_ids"] == 0


def test_id_seen_only_on_a_malformed_line_counts_as_missing_and_malformed():
    m, t = feed([10, 11])
    m.add_event({"kind": "malformed_record", "reason": "invalid_payload", "id_if_parseable": 12,
                 "host_monotonic_ns": t})
    feed([13], start_ns=t + 1, m=m)
    s = m.summary(1.0)
    assert s["malformed_lines"] == 1
    assert (s["expected_ids"], s["missing_ids"]) == (4, 1)


def test_disconnect_gap_is_recorded_and_not_counted_as_loss():
    m, t = feed(range(0, 100))
    m.add_event({"kind": "receiver_disconnected", "host_time_utc": "2026-11-25T10:00:01Z",
                 "host_monotonic_ns": t})
    t += 3 * NS
    m.add_event({"kind": "receiver_reconnected", "host_time_utc": "2026-11-25T10:00:04Z",
                 "host_monotonic_ns": t, "transport_port": "COM12"})
    feed(range(370, 470), start_ns=t + 1, m=m)
    s = m.summary(10.0)
    assert s["missing_ids"] == 0
    assert s["expected_ids"] == 200
    assert s["sender_restarts"] == 0
    assert s["disconnect_intervals"] == [{"disconnected_utc": "2026-11-25T10:00:01Z",
                                          "reconnected_utc": "2026-11-25T10:00:04Z",
                                          "duration_s": 3.0}]


def test_disconnect_without_reconnect_stays_open():
    m, t = feed(range(10))
    m.add_event({"kind": "receiver_disconnected", "host_time_utc": "2026-11-25T10:00:00Z",
                 "host_monotonic_ns": t})
    gap = m.summary(1.0)["disconnect_intervals"]
    assert gap == [{"disconnected_utc": "2026-11-25T10:00:00Z", "reconnected_utc": None,
                    "duration_s": None}]


def test_sender_restart_during_a_disconnect_is_counted():
    m, t = feed(range(500, 600))
    m.add_event({"kind": "receiver_disconnected", "host_monotonic_ns": t})
    m.add_event({"kind": "receiver_reconnected", "host_monotonic_ns": t + NS})
    feed(range(1, 11), start_ns=t + NS + 1, m=m)
    s = m.summary(5.0)
    assert s["sender_restarts"] == 1
    assert s["missing_ids"] == 0


def test_stall_and_receiver_restart_do_not_split_a_run():
    m, t = feed(range(0, 50))
    m.add_event({"kind": "receiver_stalled", "seconds_without_records": 2.0, "host_monotonic_ns": t})
    m.add_event({"kind": "receiver_resumed", "stalled_for_s": 2.5, "host_monotonic_ns": t + 1})
    m.add_event({"kind": "receiver_restarted", "warmup_records": 100, "host_monotonic_ns": t + 2})
    feed(range(80, 100), start_ns=t + 3, m=m)
    s = m.summary(5.0)
    assert (s["expected_ids"], s["missing_ids"]) == (100, 30)  # ids 50..79 were not received
    assert s["sender_restarts"] == 0
    assert s["disconnect_intervals"] == []


def test_receiver_with_no_records_reports_nothing_measured():
    s = ReceiverMetrics("rx2").summary()
    assert s["accepted_records"] == 0
    assert s["first_id"] is None and s["last_id"] is None
    assert s["measured_packet_rate_hz"] is None and s["estimated_loss_percent"] is None
    assert compare_nfr09(s)["verdict"] == "not measured"


def test_default_interval_is_the_span_of_accepted_records():
    m, _ = feed(range(91), rate_hz=91.0)  # 91 records, 90 intervals of 1/91 s
    s = m.summary()
    assert s["rate_interval_s"] == pytest.approx(90 / 91, abs=1e-3)


@pytest.mark.parametrize("rate, loss, verdict", [
    (90.0, 1.06, "met"),        # GES-68 hardware check
    (79.9, 0.0, "not met"),
    (90.0, 5.0, "not met"),     # loss must be below 5 %
    (None, 1.0, "not measured"),
])
def test_nfr09_comparison(rate, loss, verdict):
    assert compare_nfr09({"measured_packet_rate_hz": rate, "estimated_loss_percent": loss})["verdict"] == verdict


def write_capture(tmp_path, records_by_rx, events):
    (tmp_path / "receivers").mkdir()
    for rid, records in records_by_rx.items():
        with (tmp_path / "receivers" / f"{rid}.ndjson").open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps({"receiver_id": rid, **r}) + "\n")
    with (tmp_path / "events.ndjson").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_capture_directory_keeps_two_receivers_apart(tmp_path):
    rx1 = [{"id": i, "host_monotonic_ns": i * 11_000_000} for i in range(100)]
    rx2 = [{"id": i, "host_monotonic_ns": i * 11_000_000 + 1} for i in range(100) if i % 10]
    events = [{"receiver_id": "rx2", "kind": "malformed_record", "id_if_parseable": None,
               "host_monotonic_ns": 5}]
    write_capture(tmp_path, {"rx1": rx1, "rx2": rx2}, events)
    pm = summarize_capture(tmp_path, interval_s=1.1)
    assert set(pm) == {"rx1", "rx2"}
    assert (pm["rx1"]["missing_ids"], pm["rx1"]["malformed_lines"]) == (0, 0)
    assert pm["rx2"]["malformed_lines"] == 1
    assert pm["rx2"]["missing_ids"] == 9  # 0 is missing too but lies before the first accepted id


def test_capture_directory_disconnect_order_follows_monotonic_time(tmp_path):
    rx1 = [{"id": i, "host_monotonic_ns": i} for i in range(10)]
    rx1 += [{"id": i, "host_monotonic_ns": 1000 + i} for i in range(50, 60)]
    events = [{"receiver_id": "rx1", "kind": "receiver_disconnected", "host_monotonic_ns": 9},
              {"receiver_id": "rx1", "kind": "receiver_reconnected", "host_monotonic_ns": 1000}]
    write_capture(tmp_path, {"rx1": rx1}, events)
    m = summarize_capture(tmp_path, interval_s=1.0)["rx1"]
    assert m["missing_ids"] == 0
    assert len(m["disconnect_intervals"]) == 1


def test_cli_writes_packet_metrics_into_session_json(tmp_path, capsys):
    write_capture(tmp_path, {"rx1": [{"id": i, "host_monotonic_ns": i * NS // 90} for i in range(90)]}, [])
    (tmp_path / "session.json").write_text(json.dumps({"session_id": "S20261125-100000",
                                                       "packet_metrics": None}), encoding="utf-8")
    assert main([str(tmp_path), "--interval-s", "1", "--write-session"]) == 0
    session = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert session["session_id"] == "S20261125-100000"
    assert session["packet_metrics"]["rx1"]["measured_packet_rate_hz"] == 90.0
    assert "NFR-09 met" in capsys.readouterr().out


def test_cli_refuses_to_create_session_json(tmp_path):
    write_capture(tmp_path, {"rx1": [{"id": 0, "host_monotonic_ns": 0}]}, [])
    assert main([str(tmp_path), "--write-session"]) == 2
    assert not (tmp_path / "session.json").exists()
