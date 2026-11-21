# CSI stream and session contract (draft v1)

**Jira:** GES-67. **Schema version:** `1.0.0`. This contract is for the ESP32-S3 sender/receiver, host collection tool, and later evaluation. Firmware, host reader, and collection contributors must review it before implementation. It does not certify that the current boards emit this format; both boards still run the factory RGB demo.

## 1. Receiver wire record

The candidate wire format is the `csi_recv_ab` ESP32-S3 CSV output in the team's ESP-CSI overlay, at 921600 baud. It is a UTF-8 line terminated by `\n`, parsed with a real CSV parser because the final `data` field is quoted and contains commas. The first row below is a header emitted on startup. The 25 fields are in this exact order:

| # | Field | Origin | Meaning |
|---|---|---|---|
| 1 | `type` | Receiver firmware | Literal `CSI_DATA`. |
| 2 | `id` | Sender payload, extracted by receiver firmware | Unsigned sender packet sequence number. It is **not** receiver identity. |
| 3 | `mac` | Receiver firmware | Sender MAC from received packet; do not use as participant identity. |
| 4–16 | `rssi`, `rate`, `sig_mode`, `mcs`, `bandwidth`, `smoothing`, `not_sounding`, `aggregation`, `stbc`, `fec_coding`, `sgi`, `noise_floor`, `ampdu_cnt` | Receiver firmware | ESP-IDF receive metadata, represented as integers. `bandwidth` is the raw firmware field, not a string such as `HT20`. |
| 17–22 | `channel`, `secondary_channel`, `local_timestamp`, `ant`, `sig_len`, `rx_format` | Receiver firmware | More receive metadata. `local_timestamp` is the receiver's local clock, not UTC and not synchronized across receivers. Confirm its units and rollover in GES-68 before relying on it for timing. |
| 23 | `len` | Receiver firmware | Number of CSI values in `data`. |
| 24 | `first_word` | Receiver firmware | `first_word_invalid` flag (0 or 1). Retain the flag; downstream processing decides whether to discard the first word. |
| 25 | `data` | Receiver firmware | Quoted bracketed list of signed decimal byte values, e.g. `"[1,-2,3,-4]"`; preserve order and raw values. |

Wire header:

```csv
type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data
```

The current overlay derives `id` by reading four bytes at `info->payload + 15` without a visible payload-length check. GES-68 must verify the sender actually writes a monotonically increasing unsigned sequence at that offset, its byte order and reset behavior, and add the needed bounds check before the contract is treated as final. The overlay also prints `rx_ctrl->sig_mode` in both the `sig_mode` and `rx_format` positions; the firmware reviewer must decide whether the latter should use a different ESP-IDF field. Until then, the host preserves `rx_format` as an opaque integer. A receiver must emit only one complete CSV line per packet. If firmware changes this order or representation, increment the schema version and update the fixture and both consumers together.

## 2. Host record and error handling

The host assigns stable `receiver_id` values (`rx1`, `rx2`) to physical boards in `session.json`; a COM port is only a temporary transport endpoint. The host adds `host_time_utc` (RFC 3339 UTC) and `host_monotonic_ns` at receipt, and never claims that the firmware clock is UTC. Each accepted packet is written as one JSON line in `receivers/<receiver_id>.ndjson` with `schema_version`, `receiver_id`, both host times, all 25 parsed wire fields, and `raw_line`. `data` is an integer array, not a string. Keep the original `id` field; do not replace it with a host counter.

The host accepts a packet only if CSV parsing yields exactly 25 fields, `type` is `CSI_DATA`, integer fields parse, `mac` has six hex octets, `data` is a bracketed list of signed bytes, and its element count equals nonnegative `len`. Other serial lines (boot logs, the header, diagnostics) are not packet records. Malformed `CSI_DATA` lines are not silently dropped: append an event to `events.ndjson` with host time, receiver ID, `kind: "malformed_record"`, a short reason and the raw line. Do not fabricate CSI samples. On read failure or disconnect, append `receiver_disconnected`; on reconnection append `receiver_reconnected` and record the new port. Mark affected time ranges as incomplete. Keep the session directory and prior valid records even if capture stops early.

The packet-metrics component (GES-70) computes per-receiver packet loss from unique sender sequence IDs within a contiguous run: `expected = last_id - first_id + 1`, `missing = expected - unique_ids`. Record duplicates and resets separately. Do not count an unknown interval across sender reset, receiver disconnect, or sequence wrap as ordinary packet loss. If two receivers observe the same sender packet, their equal `id` values support pairing; `receiver_id` keeps their streams distinct. Because the sender sequence implementation has not yet been verified, packet-loss numbers remain provisional until GES-68 tests it.

## 3. Session layout and metadata

All real recordings live outside the public repository, using this relative layout inside the private dataset root:

```text
sessions/<YYYY-MM-DD>_<session-id>/
  session.json
  receivers/rx1.ndjson
  receivers/rx2.ndjson       # only when a second receiver is used
  trials.csv                # only for cued trials
  events.ndjson
```

`session.json` is created before capture and finalized afterward. Required keys:

| Key | Meaning |
|---|---|
| `schema_version` | `1.0.0` for this contract. |
| `session_id`, `recording_date`, `start_time_utc`, `end_time_utc` | Unique session and time range. Date uses `YYYY-MM-DD`; times use RFC 3339 UTC. |
| `participant_code`, `room_id` | Coded participant (`P1`–`P5`) and stable coded room. No names or addresses. |
| `session_type` | `cued_trials` or `continuous_no_gesture`. |
| `sender` | Coded board ID, firmware version/commit, channel and configured packet rate. |
| `receivers` | Each stable receiver ID, coded board ID, firmware version/commit, transport port used, channel and antenna/gain settings. |
| `placement` | Measured sender/receiver positions, separation, heights, antenna orientation and a coded setup description. |
| `other_people_present` | Boolean; optionally a count. Never their identities. |
| `packet_metrics` | Per-receiver observed packets, unique IDs, duplicates, malformed lines, disconnect intervals, measured packet rate and estimated loss, with an explicit `provisional` flag until sequence validation. |
| `collection_tool_version` | Host reader version/commit. |

`trials.csv` has `session_id,trial_id,class_label,cue_start_utc,cue_end_utc,failed,failed_reason` in that order. Its `session_id` matches `session.json`; the receiver streams for the trial are the files under that session's `receivers/` directory and are identified by `receiver_id` in each packet. Allowed labels are `push`, `slow_wave`, `fast_wave`, `no_gesture`; `failed` is boolean. Keep failed trials in raw data and exclude them from training/evaluation. A continuous recording has no `trials.csv` and must have `session_type: continuous_no_gesture`.

For held-out-day evaluation, group by `recording_date` and `session_id`; for held-out-participant evaluation, group by `participant_code`; for held-out-room evaluation, group by `room_id`. No window or trial from a held-out group may appear in training. A separate private code-to-name mapping must never appear in session files.

## 4. Private data and consent

Two separate folders now exist at the root of the team lead's university OneDrive: `Gestura CSI Recordings` for coded session data and `Gestura Signed Consent` for signed forms and the separate code-to-name mapping. They are not children of a shared parent folder. The folder owner has granted team-member access to both folders, consistent with the [recording procedure](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form). Before participant collection, verify the exact permission list for each folder and record the folder URLs, owner, and access decision in private team documentation. Do not enable public/anyone links or put private folder URLs or the code-to-name mapping in this public repository. Do not commit real recordings, signed forms, or code-to-name mappings. The public repository ignores `data/*`; synthetic fixtures belong under `tests/fixtures/` and contain no real participant data.

## 5. Synthetic example and validation

`tests/fixtures/ges67_wire_v1.csv` contains two valid and two intentionally malformed `CSI_DATA` rows. `tests/fixtures/ges67_expected_v1.json` states the expected accepted records and errors. These short samples are synthetic and are not a participant recording. GES-69 should use them for parser tests. GES-68 should compare actual board output against the header and field semantics before treating the contract as reviewed.

## Review checklist

- Firmware contributor: verify sender sequence offset, byte order, rollover/reset, CSI `data` units and `local_timestamp` units, field order, and baud rate.
- Collection/evaluation contributor: verify host times, receiver mapping, trial labels, event handling, metrics, and leakage-safe grouping.
- Team lead: record exact private dataset and consent locations, access list, and both review outcomes before merging.
