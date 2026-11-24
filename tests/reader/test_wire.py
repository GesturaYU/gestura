"""Parser tests against the GES-67 synthetic fixture and each contract reason code."""
import json
from pathlib import Path

import pytest

from gestura.acquisition.reader import wire
from gestura.acquisition.reader.wire import Ignored, Malformed, WireRecord, classify_line

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

VALID = ('CSI_DATA,100,1a:00:00:00:00:00,-48,11,1,0,0,1,1,0,0,0,0,-96,0,11,0,123456,0,47,0,4,0,'
         '"[3,-1,2,0]"')


def _with(line=VALID, **changes):
    """Return VALID with some fields replaced (by name, wire text)."""
    head, data = line.rsplit(',"', 1)
    values = head.split(",") + ['"' + data]
    for name, text in changes.items():
        values[wire.FIELDS.index(name)] = text
    return ",".join(values)


def test_fixture_matches_expected_result():
    expected = json.loads((FIXTURES / "ges67_expected_v1.json").read_text(encoding="utf-8"))
    lines = (FIXTURES / "ges67_wire_v1.csv").read_text(encoding="utf-8").splitlines(keepends=True)

    ignored, records, events = [], [], []
    for number, line in enumerate(lines, start=1):
        result = classify_line(line)
        if isinstance(result, Ignored):
            ignored.append({"line": number, "kind": result.kind})
        elif isinstance(result, WireRecord):
            records.append({"line": number, **result.fields})
        else:
            events.append({"line": number, "kind": "malformed_record", "reason": result.reason,
                           "id_if_parseable": result.id_if_parseable})

    assert expected["schema_version"] == wire.SCHEMA_VERSION
    assert ignored == expected["ignored_lines"]
    assert records == expected["records"]
    assert events == expected["events"]


def test_record_fields_are_in_contract_order():
    result = classify_line(VALID)
    assert list(result.fields) == list(wire.FIELDS)
    assert len(wire.FIELDS) == 25


def test_crlf_line_ending_is_removed():
    result = classify_line(VALID + "\r\n")
    assert isinstance(result, WireRecord)
    assert result.fields["data"] == [3, -1, 2, 0]


@pytest.mark.parametrize("line, kind", [
    ("I (612) csi_recv_ab: ================ CSI RECV ================", "log"),
    (",".join(wire.FIELDS), "header"),
    ("", "log"),
    ("E (30012) task_wdt: Task watchdog got triggered.", "log"),
])
def test_non_record_lines_are_ignored(line, kind):
    assert classify_line(line) == Ignored(kind)


def test_negative_unsigned_fields_are_converted():
    # Older firmware printed id and local_timestamp with %d.
    result = classify_line(_with(id="-2", local_timestamp="-1"))
    assert result.fields["id"] == 2 ** 32 - 2
    assert result.fields["local_timestamp"] == 2 ** 32 - 1


def test_other_signed_fields_stay_negative():
    result = classify_line(VALID)
    assert result.fields["rssi"] == -48
    assert result.fields["noise_floor"] == -96


@pytest.mark.parametrize("line, reason", [
    (VALID.replace(",0,47,", ",0,47,0,", 1), wire.WRONG_FIELD_COUNT),
    (VALID.rsplit(",", 5)[0], wire.WRONG_FIELD_COUNT),
    (_with(rssi="-4x"), wire.INVALID_INTEGER),
    (_with(id="12.5"), wire.INVALID_INTEGER),
    (_with(len="-4"), wire.INVALID_INTEGER),
    (_with(mac="1a:00:00:00:00"), wire.INVALID_MAC),
    (_with(mac="1a-00-00-00-00-00"), wire.INVALID_MAC),
    (VALID.replace('"[3,-1,2,0]"', '"3,-1,2,0"'), wire.INVALID_PAYLOAD),
    (VALID.replace('"[3,-1,2,0]"', '"[3,-1,2,0"'), wire.INVALID_PAYLOAD),
    (VALID.replace('"[3,-1,2,0]"', '"[3,-1,2,0'), wire.INVALID_PAYLOAD),
    (VALID.replace('"[3,-1,2,0]"', '"[3,-1,a,0]"'), wire.INVALID_PAYLOAD_INTEGER),
    (VALID.replace('"[3,-1,2,0]"', '"[3,,2,0]"'), wire.INVALID_PAYLOAD_INTEGER),
    (VALID.replace('"[3,-1,2,0]"', '"[3,-129,2,0]"'), wire.PAYLOAD_VALUE_OUT_OF_RANGE),
    (VALID.replace('"[3,-1,2,0]"', '"[3,128,2,0]"'), wire.PAYLOAD_VALUE_OUT_OF_RANGE),
    (VALID.replace('"[3,-1,2,0]"', '"[3,-1,2]"'), wire.PAYLOAD_LENGTH_MISMATCH),
    (VALID.replace('"[3,-1,2,0]"', '"[3,-1,2,0,1]"'), wire.PAYLOAD_LENGTH_MISMATCH),
])
def test_each_reason_code(line, reason):
    result = classify_line(line)
    assert isinstance(result, Malformed)
    assert result.reason == reason


def test_first_failing_check_gives_the_reason():
    # Invalid MAC and a length mismatch: the MAC check comes first.
    line = _with(mac="zz:00:00:00:00:00").replace('"[3,-1,2,0]"', '"[3,-1]"')
    assert classify_line(line).reason == wire.INVALID_MAC
    # Invalid integer and an out-of-range payload: the integer check comes first.
    line = _with(rssi="x").replace('"[3,-1,2,0]"', '"[300,-1,2,0]"')
    assert classify_line(line).reason == wire.INVALID_INTEGER


def test_id_if_parseable_is_null_when_id_is_not_an_integer():
    result = classify_line(_with(id="abc"))
    assert result == Malformed(wire.INVALID_INTEGER, None)


def test_id_if_parseable_is_kept_for_a_cut_line():
    result = classify_line("CSI_DATA,77,1a:00:00:00:00:00,-48,11,1,0")
    assert result == Malformed(wire.WRONG_FIELD_COUNT, 77)


def test_empty_payload_with_len_zero_is_valid():
    line = _with(len="0").replace('"[3,-1,2,0]"', '"[]"')
    assert classify_line(line).fields["data"] == []
