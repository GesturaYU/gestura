"""Parse and validate one receiver serial line (docs/csi-session-contract-v1.md, Sections 1-2).

This module has no I/O. ``classify_line`` turns one line of receiver output into one of:

- ``Ignored``: not a packet record (boot log, banner, header row, other console output);
- ``WireRecord``: an accepted CSI_DATA record with all 25 wire fields parsed;
- ``Malformed``: a CSI_DATA line that failed validation, with the contract's reason code.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Optional, Union

SCHEMA_VERSION = "1.0.0"

FIELDS = (
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs", "bandwidth", "smoothing",
    "not_sounding", "aggregation", "stbc", "fec_coding", "sgi", "noise_floor", "ampdu_cnt",
    "channel", "secondary_channel", "local_timestamp", "ant", "sig_len", "rx_state", "len",
    "first_word", "data",
)
FIELD_COUNT = len(FIELDS)  # 25
# Fields 2 and 4-24 (1-based) are integers: every field except type, mac and data.
INTEGER_FIELDS = tuple(f for f in FIELDS if f not in ("type", "mac", "data"))
# Unsigned 32-bit fields that the pre-GES-68 firmware printed with %d.
UNSIGNED_FIELDS = ("id", "local_timestamp")

RECORD_PREFIX = "CSI_DATA"
HEADER_PREFIX = "type,id,mac,"

# Reason codes, in the order the contract checks them.
WRONG_FIELD_COUNT = "wrong_field_count"
INVALID_INTEGER = "invalid_integer"
INVALID_MAC = "invalid_mac"
INVALID_PAYLOAD = "invalid_payload"
INVALID_PAYLOAD_INTEGER = "invalid_payload_integer"
PAYLOAD_VALUE_OUT_OF_RANGE = "payload_value_out_of_range"
PAYLOAD_LENGTH_MISMATCH = "payload_length_mismatch"

_INT_RE = re.compile(r"-?[0-9]+")
_MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")


@dataclass(frozen=True)
class Ignored:
    kind: str  # "header" or "log"


@dataclass(frozen=True)
class WireRecord:
    fields: dict  # the 25 wire fields in contract order; data is a list of int


@dataclass(frozen=True)
class Malformed:
    reason: str
    id_if_parseable: Optional[int]


LineResult = Union[Ignored, WireRecord, Malformed]


def strip_line_ending(line: str) -> str:
    """Remove the trailing newline and one trailing carriage return."""
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def is_header(line: str) -> bool:
    return line.startswith(HEADER_PREFIX)


def is_record_line(line: str) -> bool:
    return line.startswith(RECORD_PREFIX)


def id_if_parseable(line: str) -> Optional[int]:
    """The text between the first and second comma, if it is a decimal integer."""
    parts = line.split(",", 2)
    if len(parts) < 3 or not _INT_RE.fullmatch(parts[1]):
        return None
    return _unsigned(int(parts[1]))


def _unsigned(value: int) -> int:
    # A negative value v printed by %d is the unsigned value v + 2**32; the conversion is lossless.
    return value + 2 ** 32 if value < 0 else value


def classify_line(line: str) -> LineResult:
    """Classify one line of receiver output. ``line`` may still end in \\n or \\r\\n."""
    line = strip_line_ending(line)
    if is_header(line):
        return Ignored("header")
    if not is_record_line(line):
        return Ignored("log")
    reason, fields = _validate(line)
    if reason is not None:
        return Malformed(reason, id_if_parseable(line))
    return WireRecord(fields)


def _validate(line: str):
    """Return (reason, None) for the first failing check, or (None, fields)."""
    try:
        # One line on its own; strict=True reports an unterminated quote instead of guessing.
        values = next(csv.reader([line], strict=True))
    except csv.Error:
        # A cut or interleaved line leaves the data quote open.
        return INVALID_PAYLOAD, None
    if len(values) != FIELD_COUNT:
        return WRONG_FIELD_COUNT, None

    raw = dict(zip(FIELDS, values))
    fields: dict = {"type": raw["type"]}
    for name in INTEGER_FIELDS:
        if not _INT_RE.fullmatch(raw[name]):
            return INVALID_INTEGER, None
    if int(raw["len"]) < 0:
        return INVALID_INTEGER, None
    if not _MAC_RE.fullmatch(raw["mac"]):
        return INVALID_MAC, None

    data_text = raw["data"]
    if not (data_text.startswith("[") and data_text.endswith("]")) or "[" in data_text[1:] \
            or "]" in data_text[:-1]:
        return INVALID_PAYLOAD, None
    inner = data_text[1:-1]
    elements = [] if inner.strip() == "" else inner.split(",")
    payload = []
    for element in elements:
        element = element.strip()
        if not _INT_RE.fullmatch(element):
            return INVALID_PAYLOAD_INTEGER, None
        payload.append(int(element))
    if any(v < -128 or v > 127 for v in payload):
        return PAYLOAD_VALUE_OUT_OF_RANGE, None
    if len(payload) != int(raw["len"]):
        return PAYLOAD_LENGTH_MISMATCH, None

    for name in FIELDS:
        if name in INTEGER_FIELDS:
            value = int(raw[name])
            fields[name] = _unsigned(value) if name in UNSIGNED_FIELDS else value
        elif name == "mac":
            fields[name] = raw[name]
        elif name == "data":
            fields[name] = payload
    return None, {name: fields[name] for name in FIELDS}
