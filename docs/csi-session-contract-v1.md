# CSI stream and session contract (v1)

**Jira:** GES-67. **Schema version:** `1.0.0`. This contract covers the ESP32-S3 sender and receiver, the host reader (GES-69), packet metrics (GES-70), the recording flow (GES-71), metadata validation (GES-72) and evaluation splits (GES-74). Section 6 lists what is verified on source and hardware and what GES-68 changed in the firmware.

## 1. Receiver wire record

The receiver firmware is `firmware/receiver` (the team's `csi_recv_ab` overlay on the Espressif esp-csi `csi_recv` example, esp-csi commit `8633d67`, ESP-IDF v5.5.6), paired with `firmware/sender` (`csi_send_ch11_ht20`): channel 11, 20 MHz (HT20), ESP-NOW broadcast, 100 packets/s configured. The receiver writes to its UART console at 921600 baud (8N1). On the current boards this is the USB-to-UART (CH343) port, not the native USB port.

Each packet is one UTF-8 line ending in `\n`. The final `data` field is quoted and contains commas. The 25 fields are in this exact order:

| # | Field | Origin | Meaning |
|---|---|---|---|
| 1 | `type` | Receiver firmware | Literal `CSI_DATA`. |
| 2 | `id` | Sender, copied by receiver | Sender packet sequence number: a `uint32` counter that starts at 0 when the sender boots and increases by 1 per send. The first `id` a receiver sees after a sender boot may be above 0 (it was 1 in the GES-68 check). It is **not** receiver identity. |
| 3 | `mac` | Receiver firmware | Source MAC of the received packet (the sender, `1a:00:00:00:00:00` in the current firmware). It does not identify the receiver; see Section 2. |
| 4–16 | `rssi`, `rate`, `sig_mode`, `mcs`, `bandwidth`, `smoothing`, `not_sounding`, `aggregation`, `stbc`, `fec_coding`, `sgi`, `noise_floor`, `ampdu_cnt` | Receiver firmware | ESP-IDF receive metadata as integers. `bandwidth` is the raw field (0 = 20 MHz, 1 = 40 MHz), not a string such as `HT20`. |
| 17–21 | `channel`, `secondary_channel`, `local_timestamp`, `ant`, `sig_len` | Receiver firmware | `secondary_channel`: 0 none, 1 above, 2 below. `local_timestamp` is ESP-IDF `rx_ctrl.timestamp`: an unsigned 32-bit count of microseconds since the receiver booted. It wraps to 0 about every 71.6 min, is not UTC and is not synchronized between receivers. |
| 22 | `rx_state` | Receiver firmware | ESP-IDF `rx_ctrl.rx_state`: 0 means no error; other values are undocumented error codes. |
| 23 | `len` | Receiver firmware | Number of values in `data` (bytes of CSI). |
| 24 | `first_word` | Receiver firmware | `first_word_invalid` flag (0 or 1). Kept as is; downstream processing decides whether to discard the first four bytes. |
| 25 | `data` | Receiver firmware | Quoted bracketed list of raw signed byte values, e.g. `"[3,-1,2,0]"`. |

Header row:

```csv
type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_state,len,first_word,data
```

**Start-up.** After boot the receiver prints an `ESP_LOGI` banner (`================ CSI RECV ================`) and the header row just before its first CSI record. For its first 100 CSI packets it measures the automatic gain; from the 100th packet on it fixes the gain at that baseline (`CONFIG_FORCE_GAIN=1`). The gain values are not printed on ESP32-S3, so the host recognises the warm-up only by counting records after a header row. The gain component prints one `wifi_rx_gain: agc_gain(median): …` log line after about 50 records; it is not the point at which the gain is fixed and is ignored like other log lines. When the host connects to a receiver that is already running, it sees no header and the gain state is unknown.

**CSI data layout.** Each subcarrier is two signed bytes: imaginary part first, then real part (ESP-IDF Wi-Fi guide, *Wi-Fi Channel State Information*). For the project configuration (HT packet, 20 MHz, `secondary_channel` 0, `stbc` 0), `len` is 256: 64 LLTF subcarriers followed by 64 HT-LTF subcarriers, each block in the order 0…31, then −32…−1. Values are raw. They are comparable only after the gain warm-up.

If firmware changes the field order or representation, the schema version is increased and the fixtures, this document and both consumers are updated together.

## 2. Host records and error handling

**Receiver identity.** The host identifies each physical receiver by the USB serial number the operating system reports for its port. Each board carries a written label (`B1`, `B2`, …). A host configuration file maps each label to its USB serial number, and `session.json` maps the label to a stable `receiver_id` (`rx1`, `rx2`). A COM port name is only a temporary transport endpoint and is recorded but never used as identity. At session start the host checks that every configured serial number is present exactly once. If the USB-to-UART bridge does not report a unique serial number (checked in the GES-68 hardware check), GES-68 adds a boot line with the receiver's factory (eFuse) MAC and the host uses that instead.

**Framing.** The host splits the byte stream on `\n`, removes a trailing `\r` (fixture files may be checked out with CRLF on Windows), and parses each line on its own with a CSV parser. It must not pass the stream to a multi-line CSV reader: a cut or interleaved line leaves the `data` quote open, and such a reader would merge the next valid record into the broken one.

**Accepted records.** Each accepted packet is written as one JSON line in `receivers/<receiver_id>.ndjson` with `schema_version`, `receiver_id`, `host_time_utc` (RFC 3339 UTC at receipt), `host_monotonic_ns` (host monotonic clock at receipt), all 25 parsed wire fields and `raw_line`. `data` is an integer array, not a string. The original `id` is kept; it is never replaced by a host counter. The host never presents `local_timestamp` as UTC.

**Validation.** A `CSI_DATA` line is checked in this order; the first failing check gives the reason code:

| Reason | Rule |
|---|---|
| `wrong_field_count` | CSV parsing yields a number of fields other than 25. |
| `invalid_integer` | A field among 2 and 4–24 is not a decimal integer, or `len` is negative. |
| `invalid_mac` | `mac` is not six two-digit hex octets separated by `:`. |
| `invalid_payload` | `data` is not one complete bracketed, comma-separated list. This covers lines that were cut off or interleaved with other console output (an unterminated quote); if the CSV parser itself reports the unterminated quote, the reason is also `invalid_payload`. |
| `invalid_payload_integer` | An element of `data` is not a decimal integer. |
| `payload_value_out_of_range` | An element is outside −128…127. |
| `payload_length_mismatch` | The number of elements differs from `len`. |

`id` and `local_timestamp` are unsigned. The firmware in `firmware/receiver` prints both with `PRIu32`. Builds before GES-68 printed them with `%d`, so in their output `local_timestamp` appears negative after about 35.8 min of receiver uptime; the host converts a negative value `v` to `v + 2^32`, which is lossless, and does not compute intervals across a wrap without unwrapping.

**Events.** `events.ndjson` holds one JSON object per line with `host_time_utc`, `host_monotonic_ns`, `receiver_id`, `kind` and kind-specific fields:

| `kind` | When |
|---|---|
| `malformed_record` | A `CSI_DATA` line fails validation. Adds `reason` (table above), `raw_line` and `id_if_parseable`: the text between the first and second comma if it is a decimal integer, otherwise `null`. Malformed lines are never dropped silently and never turned into CSI samples. |
| `receiver_restarted` | A header row (any line starting with `type,id,mac,`) arrives after capture has started. The next 100 records are gain warm-up records. |
| `config_mismatch` | An accepted record has `bandwidth`, `secondary_channel`, `stbc` or `channel` different from the session's configuration. Logged when the value changes, not for every record; the record is still kept. |
| `receiver_disconnected` / `receiver_reconnected` | A read fails or the port disappears / the receiver is back. Reconnection records the port used. |
| `receiver_stalled` / `receiver_resumed` | The port is still open, but no `CSI_DATA` line has arrived for the reader's stall timeout (default 2 s; other serial lines do not count) / `CSI_DATA` lines arrive again. `receiver_stalled` adds `seconds_without_records` and `transport_port`; `receiver_resumed` adds `stalled_for_s`. A stall does not split a run in the packet metrics: if the sender restarted, the lower `id` already starts a new run. |

Other serial lines (boot logs, banners, the header, diagnostics) are not packet records. The session directory and earlier valid records are kept even if capture stops early.

**Packet metrics (GES-70).** Per receiver, loss is computed from sender IDs within a contiguous run: `expected = last_id - first_id + 1`, `missing = expected - unique_ids`. An `id` seen only on a malformed line counts as missing, and the line is also counted under `malformed_lines`. Duplicates are counted separately. An `id` lower than the previous one (sender restart) starts a new run. Intervals across a sender restart, receiver disconnect or sequence wrap are not counted as ordinary loss. Packet rate is measured from accepted records over a stated interval; the configured 100 packets/s is not the expected rate, because the sender produced about 91 IDs/s in GES-48. When two receivers see the same sender packet, their equal `id` values allow pairing, and `receiver_id` keeps the streams apart. The GES-68 hardware check confirmed this sequence behaviour on the current firmware (Section 6). `provisional` is `true` for values that are not final: placeholders written before capture and values read while capture is running. Values computed after capture are final (`false`).

## 3. Session layout and metadata

All real recordings live outside the public repository, in this layout inside the private dataset root:

```text
sessions/<YYYY-MM-DD>_<session_id>/
  session.json
  receivers/rx1.ndjson
  receivers/rx2.ndjson       # only when a second receiver is used
  trials.csv                # only for cued trials
  events.ndjson
```

A session is one participant recording in one room on one day ([recording procedure](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form), Section 4). `session.json` is created before capture and finalized afterward. Required keys:

| Key | Meaning |
|---|---|
| `schema_version` | `1.0.0`. |
| `session_id` | Generated by the collection tool from the UTC start time: `S<YYYYMMDD>-<HHMMSS>`, e.g. `S20261121-090000`. Contains no participant or room information. |
| `recording_date` | Local calendar date (Europe/Istanbul) of the session start, `YYYY-MM-DD`. Also the directory prefix. |
| `start_time_utc`, `end_time_utc` | RFC 3339 UTC. |
| `participant_code` | `P1`–`P5`. For a continuous recording, the member who records it. |
| `room_id`, `room_type`, `room_role` | `room_id` is `R<n>` (`R1`, `R2`, …) from the private room list (Section 4). `room_type` is `residential` or `campus`. `room_role` is `dataset` or `final_demo`; a `final_demo` session is never used for training. |
| `session_type` | `cued_trials` or `continuous_no_gesture`. |
| `sender` | `board_id`, `firmware_commit`, `channel`, `bandwidth_mhz`, `configured_packet_rate_hz`. |
| `receivers` | List; each has `receiver_id`, `board_id`, `usb_serial_number`, `transport_port`, `firmware_commit`, `channel`, `bandwidth_mhz`, `gain_setting`. |
| `placement` | `description` (coded, no address), `sender_height_m`, and per `receiver_id`: `distance_from_sender_m`, `height_m`, `antenna_orientation`. Distances are in metres between the centres of the boards' PCB antennas; heights are from the floor to the antenna. `antenna_orientation` is `facing_sender` or `other` with a short note. |
| `other_people_present`, `other_people_count`, `recorder_in_room` | Everyone in the room except the participant counts as another person, including the recorder. `recorder_in_room` says whether the recorder was one of them. No identities. |
| `signal_check` | `time_utc`, `interval_s`, `passed` (boolean) and per `receiver_id`: `connected` and `packet_rate_hz` over the interval. The pass criterion is defined in the recording flow (GES-71). |
| `packet_metrics` | Per `receiver_id`: `accepted_records`, `unique_sender_ids`, `duplicates`, `malformed_lines`, `first_id`, `last_id`, `expected_ids`, `missing_ids`, `sender_restarts`, `disconnect_intervals`, `rate_interval_s`, `measured_packet_rate_hz`, `estimated_loss_percent`, `provisional`. |
| `collection_tool_version` | Host reader and collection tool version or commit. |

`trials.csv` has these columns in this order:

```csv
session_id,trial_id,class_label,cue_start_utc,cue_end_utc,cue_start_monotonic_ns,cue_end_monotonic_ns,failed,failed_reason
```

Labels are matched to packets by `host_monotonic_ns`, which comes from the same host clock as the packet records; the UTC columns are for people and can jump when the computer synchronizes its clock. `session_id` matches `session.json`. Allowed labels are `push`, `slow_wave`, `fast_wave`, `no_gesture`; `failed` is `true` or `false`. Failed trials stay in the raw data and are excluded from training and evaluation. A continuous recording has no `trials.csv` and has `session_type: continuous_no_gesture`.

**Evaluation groups (GES-74).** Held-out day: group by `recording_date`, so every session of a held-out date, in any room, is excluded from training. Held-out participant: group by `participant_code`. Held-out room: group by `room_id`. Sessions with `room_role: final_demo` are never in training. No window or trial of a held-out group may appear in training.

## 4. Private data and consent

Two separate folders exist at the root of the GES-67 owner's university OneDrive: `Gestura CSI Recordings` for coded session data and `Gestura Signed Consent` for signed forms. Neither is inside the other. The owner has shared both with the other team members, as the [recording procedure](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form) requires (Section 8).

`Gestura Signed Consent` also holds the two private lists: a single list that maps each participant code to the participant's name, and the room list that maps each `room_id` to its description and marks the final demonstration room. Neither list is copied into session files or this repository. The folder URLs and the exact access list are recorded in private team documentation, not here; no public or "anyone with the link" sharing is used.

Real recordings, signed forms and the lists are never committed. The repository ignores `data/*`; synthetic fixtures live under `tests/fixtures/` and contain no participant data.

## 5. Synthetic fixtures

- `tests/fixtures/ges67_wire_v1.csv` is a short synthetic receiver stream: the start-up banner, the header, three valid records and three malformed ones (length mismatch, non-integer payload element, and a line interleaved with a task-watchdog message followed by a valid record). Payloads are shortened to four values; real 20 MHz records have `len` 256.
- `tests/fixtures/ges67_expected_v1.json` gives the expected result: the ignored lines, every accepted record with all 25 wire fields, and the `malformed_record` events with their reason codes. Host-added fields that depend on receipt time (`host_time_utc`, `host_monotonic_ns`) and `raw_line` are left out.
- `tests/fixtures/ges67_session_v1.json` is a synthetic `session.json`; its `packet_metrics` are computed from the wire fixture.

GES-69 uses the first two files for parser tests.

## 6. Evidence and open firmware changes

**Checked against source.** Field order and the `%d` printing of `id` and `local_timestamp`; 921600 baud on the UART console (`csi_recv_ab/sdkconfig.defaults`); the gain warm-up; the sender counter. The receiver reads `id` at `info->payload + 15`, which matches the 15-byte ESP-NOW vendor-specific header before the sender's data; both boards are little-endian.

**Checked against real output.** Two team captures from 29 Oct 2026 (12,516 lines, kept outside this repository) were made with an earlier 40 MHz build on channel 11. Every complete line had 25 fields; `id` only increased, with no duplicates or restarts within a capture; `local_timestamp` advanced at 1.000 µs per host-clock µs; `len` was 384 and `first_word` 0 in every row. Four lines were broken by an interleaved `Task watchdog got triggered` message at about 30 s intervals (`CONFIG_ESP_TASK_WDT_TIMEOUT_S=30`). At 40 MHz a line is about 1.2 KB and 921600 baud carries at most about 77 lines/s, which matches the loss seen there; GES-48 measured under 2 % loss at 20 MHz.

**Checked on hardware (GES-68, 23 Nov 2026).** One receiver and one sender about 1 m apart, both ESP32-S3; raw captures are kept outside this repository.

- Before the firmware changes below (receiver and sender as used in GES-48): 90 s after a receiver reset gave 8,126 records at 90.6 records/s, 0.4 % loss, no duplicates and no malformed lines. Every record had `len` 256, `bandwidth` 0, `channel` 11, `secondary_channel` 0, `stbc` 0 and `first_word` 0. Column 22 was still named `rx_format`.
- With `firmware/receiver` from this repository: 300 s after a reset gave 26,981 records at 90.0 records/s, 1.06 % loss, no duplicates and no malformed lines, with the same field values, `rx_state` 0 in every record and the header row of Section 1. No task-watchdog message appeared in either capture, so at 20 MHz none split a CSI line.
- Subcarrier order: in 2,000 records the slots that were always zero were 0 and 27–37 in the LLTF block and 64 and 93–99 in the HT-LTF block. This matches the order 0…31, −32…−1 with 52 used LLTF and 56 used HT-LTF subcarriers.
- Sender restart: the sender's power was removed for a few seconds. The receiver got no records for 8.3 s, then `id` went from 33,880 to 1 and increased again without duplicates (1.1 % loss in the new run). The new-run rule of Section 2 handles this case.
- Receiver restart: a reset during capture produced the banner and the header row about 0.35 s later, which is what `receiver_restarted` relies on.
- USB serial number: the receiver's CH343 bridge reports `5C37270507` (Windows device ID `USB\VID_1A86&PID_55D3\5C37270507`). The sender board's bridge was recorded as `5C37272594` on 17 Oct 2026. The two differ, so the eFuse MAC boot line of Section 2 is not needed.
- Not observed: the gain warm-up itself (the firmware does not print when the gain is fixed) and a `local_timestamp` wrap.

**Firmware changes made in GES-68.** `firmware/receiver` (from `csi_recv_ab`) and `firmware/sender` (from `csi_send_ch11_ht20`) are committed, so `firmware_commit` in `session.json` points to a real commit. The receiver now checks `info->payload_len >= 19` before reading the sequence number, copies it with `memcpy`, prints `id` and `local_timestamp` with `PRIu32`, and prints `rx_ctrl->rx_state` in column 22 under the name `rx_state`. A shorter payload produces no line. The host keeps accepting negative `id`/`local_timestamp` values so that output from older builds stays readable.
