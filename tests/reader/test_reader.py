"""Reader tests with fake ports: one and two receivers, gaps, malformed input, stalls, unplugging."""
import pytest

from conftest import FIXTURES, HEADER, read_ndjson, record_line, wait_until
from gestura.acquisition.reader import wire
from gestura.acquisition.reader.reader import CaptureSession, ReceiverSpec, SessionStartError

RX1 = ReceiverSpec("rx1", "B2", "SER-B2")
RX2 = ReceiverSpec("rx2", "B3", "SER-B3")


def make_session(tmp_path, bus, receivers=(RX1,), **kwargs):
    kwargs.setdefault("stall_timeout_s", 5.0)
    kwargs.setdefault("reconnect_poll_s", 0.02)
    return CaptureSession(tmp_path / "S1", list(receivers), resolver=bus.resolver,
                          opener=bus.opener, **kwargs)


def accepted(session):
    return {s.receiver_id: s.accepted_records for s in session.status()}


def events(tmp_path, kind=None):
    rows = read_ndjson(tmp_path / "S1" / "events.ndjson")
    return [e for e in rows if kind is None or e["kind"] == kind]


# -- one receiver -------------------------------------------------------------------------

def test_fixture_stream_through_one_receiver(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    port.push((FIXTURES / "ges67_wire_v1.csv").read_bytes())
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: session.status()[0].malformed_lines == 3 and accepted(session)["rx1"] == 3)
    session.stop()

    records = read_ndjson(tmp_path / "S1" / "receivers" / "rx1.ndjson")
    assert [r["id"] for r in records] == [100, 101, 105]  # original sender ids, not renumbered
    first = records[0]
    assert list(first)[:4] == ["schema_version", "receiver_id", "host_time_utc",
                               "host_monotonic_ns"]
    assert list(first)[4:-1] == list(wire.FIELDS)
    assert first["raw_line"].startswith("CSI_DATA,100,")
    assert first["schema_version"] == "1.0.0" and first["receiver_id"] == "rx1"
    assert first["host_time_utc"].endswith("Z")
    assert isinstance(first["data"], list)

    malformed = events(tmp_path, "malformed_record")
    assert [(e["reason"], e["id_if_parseable"]) for e in malformed] == [
        ("payload_length_mismatch", 102), ("invalid_payload_integer", 103),
        ("invalid_payload", 104)]
    assert all(e["raw_line"].startswith("CSI_DATA,") for e in malformed)
    # The boot banner and the header at the start are not packet records and not a restart.
    assert events(tmp_path, "receiver_restarted") == []


def test_line_split_across_reads_is_reassembled(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    line = record_line(7)
    port.push(line[:20])
    port.push(line[20:50])
    port.push(line[50:])
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: accepted(session)["rx1"] == 1)
    session.stop()
    assert events(tmp_path, "malformed_record") == []


def test_missing_and_duplicate_sequence_numbers_are_kept_as_received(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    for seq in (1, 2, 5, 5, 6, 1, 2):  # gap 3-4, a duplicate, then a sender restart
        port.push(record_line(seq))
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: accepted(session)["rx1"] == 7)
    session.stop()
    records = read_ndjson(tmp_path / "S1" / "receivers" / "rx1.ndjson")
    assert [r["id"] for r in records] == [1, 2, 5, 5, 6, 1, 2]
    assert events(tmp_path) == []  # loss and restarts are computed later (GES-70), not here


def test_header_after_capture_started_is_a_restart(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    port.push(HEADER)  # boot header before the first record: not a restart
    port.push(record_line(1))
    port.push("I (612) csi_recv_ab: ================ CSI RECV ================\n")
    port.push(HEADER)
    port.push(record_line(1))
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: accepted(session)["rx1"] == 2)
    session.stop()
    restarts = events(tmp_path, "receiver_restarted")
    assert len(restarts) == 1 and restarts[0]["warmup_records"] == 100
    assert session.status()[0].warmup_records_remaining == 99


def test_config_mismatch_is_logged_when_the_value_changes(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    for seq, bw in [(1, 0), (2, 1), (3, 1), (4, 1), (5, 0), (6, 1)]:
        port.push(record_line(seq, bandwidth=bw))
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: accepted(session)["rx1"] == 6)
    session.stop()
    mismatches = events(tmp_path, "config_mismatch")
    assert [e["id"] for e in mismatches] == [2, 6]
    assert mismatches[0]["mismatches"] == {"bandwidth": {"expected": 0, "actual": 1}}
    # The records are still kept.
    assert len(read_ndjson(tmp_path / "S1" / "receivers" / "rx1.ndjson")) == 6


def test_final_line_without_newline_is_reported_on_stop(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    port.push(record_line(1))
    port.push(record_line(2)[:30])
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: accepted(session)["rx1"] == 1)
    session.stop()
    malformed = events(tmp_path, "malformed_record")
    assert len(malformed) == 1 and malformed[0]["id_if_parseable"] == 2


# -- two receivers ------------------------------------------------------------------------

def test_two_receivers_interleaved_do_not_mix(tmp_path, bus):
    p1 = bus.plug("SER-B2", "COM10")
    p2 = bus.plug("SER-B3", "COM11")
    # Interleave whole lines and half lines between the two ports.
    for seq in range(50):
        a, b = record_line(seq, data=(1, 1, 1, 1)), record_line(1000 + seq, data=(2, 2, 2, 2))
        p1.push(a[:25])
        p2.push(b[:40])
        p1.push(a[25:])
        p2.push(b[40:])
    session = make_session(tmp_path, bus, receivers=(RX1, RX2))
    session.start()
    wait_until(lambda: accepted(session) == {"rx1": 50, "rx2": 50})
    session.stop()

    rx1 = read_ndjson(tmp_path / "S1" / "receivers" / "rx1.ndjson")
    rx2 = read_ndjson(tmp_path / "S1" / "receivers" / "rx2.ndjson")
    assert [r["id"] for r in rx1] == list(range(50))
    assert [r["id"] for r in rx2] == list(range(1000, 1050))
    assert {r["receiver_id"] for r in rx1} == {"rx1"} and {r["receiver_id"] for r in rx2} == {"rx2"}
    assert all(r["data"] == [1, 1, 1, 1] for r in rx1)
    assert all(r["data"] == [2, 2, 2, 2] for r in rx2)
    assert events(tmp_path) == []
    assert session.receivers_info() == [
        {"receiver_id": "rx1", "board_id": "B2", "usb_serial_number": "SER-B2",
         "transport_port": "COM10"},
        {"receiver_id": "rx2", "board_id": "B3", "usb_serial_number": "SER-B3",
         "transport_port": "COM11"}]


def test_unplugged_receiver_is_reported_and_the_other_continues(tmp_path, bus):
    p1 = bus.plug("SER-B2", "COM10")
    p2 = bus.plug("SER-B3", "COM11")
    session = make_session(tmp_path, bus, receivers=(RX1, RX2))
    session.start()
    p1.push(record_line(1))
    p2.push(record_line(1))
    p2.push(record_line(2)[:30])  # cut by the unplug
    wait_until(lambda: accepted(session) == {"rx1": 1, "rx2": 1})

    bus.unplug("SER-B3")
    wait_until(lambda: session.status()[1].state == "disconnected")
    for seq in range(2, 12):
        p1.push(record_line(seq))
    wait_until(lambda: accepted(session)["rx1"] == 11)  # rx1 keeps going while rx2 is gone
    assert session.status()[0].state == "connected"

    # Plugged back in on a different port name: identity follows the serial number.
    p2b = bus.plug("SER-B3", "COM14")
    wait_until(lambda: session.status()[1].state == "connected")
    p2b.push(record_line(20))
    wait_until(lambda: accepted(session)["rx2"] == 2)
    session.stop()

    kinds = [(e["receiver_id"], e["kind"]) for e in events(tmp_path)]
    assert kinds == [("rx2", "malformed_record"), ("rx2", "receiver_disconnected"),
                     ("rx2", "receiver_reconnected")]
    assert events(tmp_path, "receiver_disconnected")[0]["transport_port"] == "COM11"
    assert events(tmp_path, "receiver_reconnected")[0]["transport_port"] == "COM14"
    rx2 = read_ndjson(tmp_path / "S1" / "receivers" / "rx2.ndjson")
    assert [r["id"] for r in rx2] == [1, 20]


def test_warmup_state_is_unknown_after_a_reconnect(tmp_path, bus):
    # A replugged receiver restarts and prints its header before the port is reopened, so the
    # reader cannot know how many warm-up records remain.
    port = bus.plug("SER-B2", "COM10")
    port.push(HEADER)
    port.push(record_line(1))
    session = make_session(tmp_path, bus)
    session.start()
    wait_until(lambda: accepted(session)["rx1"] == 1)
    assert session.status()[0].warmup_records_remaining == 99

    bus.unplug("SER-B2")
    wait_until(lambda: session.status()[0].state == "disconnected")
    port2 = bus.plug("SER-B2", "COM10")
    wait_until(lambda: session.status()[0].state == "connected")
    port2.push(record_line(5))
    wait_until(lambda: accepted(session)["rx1"] == 2)
    assert session.status()[0].warmup_records_remaining is None

    port2.push(HEADER)  # a header seen after the reconnect starts a known warm-up again
    port2.push(record_line(1))
    wait_until(lambda: accepted(session)["rx1"] == 3)
    session.stop()
    assert session.status()[0].warmup_records_remaining == 99
    assert [e["kind"] for e in events(tmp_path)] == [
        "receiver_disconnected", "receiver_reconnected", "receiver_restarted"]


def test_stalled_receiver_is_reported_and_the_other_continues(tmp_path, bus):
    p1 = bus.plug("SER-B2", "COM10")
    p2 = bus.plug("SER-B3", "COM11")
    session = make_session(tmp_path, bus, receivers=(RX1, RX2), stall_timeout_s=0.3)
    session.start()
    p2.push(record_line(1))
    for seq in range(40):  # rx1 keeps sending for about 0.8 s; rx2 goes silent
        p1.push(record_line(seq))
        wait_until(lambda: accepted(session)["rx1"] == seq + 1)
        __import__("time").sleep(0.02)
    wait_until(lambda: session.status()[1].state == "stalled")
    assert session.status()[0].state == "connected"
    p2.push("E (30012) task_wdt: some log line\n")  # log output does not end a stall
    p2.push(record_line(2))
    wait_until(lambda: session.status()[1].state == "connected")
    session.stop()

    stall_events = [e for e in events(tmp_path) if e["kind"].startswith("receiver_")]
    assert [(e["receiver_id"], e["kind"]) for e in stall_events] == [
        ("rx2", "receiver_stalled"), ("rx2", "receiver_resumed")]
    assert stall_events[1]["stalled_for_s"] >= 0.3
    assert accepted(session) == {"rx1": 40, "rx2": 2}


# -- session start checks -----------------------------------------------------------------

def test_missing_receiver_stops_the_session_start(tmp_path, bus):
    bus.plug("SER-B2", "COM10")
    session = make_session(tmp_path, bus, receivers=(RX1, RX2))
    with pytest.raises(SessionStartError, match="rx2 \\(B3, serial SER-B3\\)"):
        session.start()
    assert not (tmp_path / "S1" / "events.ndjson").exists()
    assert [s.state for s in session.status()] == ["not_started", "not_started"]


def test_duplicate_serial_number_in_configuration_is_rejected(tmp_path, bus):
    with pytest.raises(ValueError, match="usb_serial_number"):
        make_session(tmp_path, bus, receivers=(RX1, ReceiverSpec("rx2", "B3", "SER-B2")))


def test_more_than_two_receivers_is_rejected(tmp_path, bus):
    with pytest.raises(ValueError, match="one or two"):
        make_session(tmp_path, bus, receivers=(RX1, RX2, ReceiverSpec("rx3", "B4", "S4")))


def test_existing_output_is_never_overwritten(tmp_path, bus):
    bus.plug("SER-B2", "COM10")
    (tmp_path / "S1" / "receivers").mkdir(parents=True)
    (tmp_path / "S1" / "receivers" / "rx1.ndjson").write_text("earlier\n")
    session = make_session(tmp_path, bus)
    with pytest.raises(SessionStartError, match="already exist"):
        session.start()
    assert (tmp_path / "S1" / "receivers" / "rx1.ndjson").read_text() == "earlier\n"
    assert bus.ports["COM10"].closed


def test_callbacks_receive_records_and_events(tmp_path, bus):
    port = bus.plug("SER-B2", "COM10")
    seen_records, seen_events = [], []
    session = make_session(tmp_path, bus, on_record=seen_records.append,
                           on_event=seen_events.append)
    session.start()
    port.push(record_line(1))
    port.push(record_line(2, data=(1, 2)).replace(",4,0,", ",4,0,", 1).replace('"[1,2]"', '"[1]"'))
    wait_until(lambda: seen_events)
    session.stop()
    assert [r["id"] for r in seen_records] == [1]
    assert seen_events[0]["kind"] == "malformed_record"
