# Packet rate and loss per receiver (GES-70)

Computes the `packet_metrics` of `session.json` for each receiver from the host reader's output
(GES-69), following [`docs/csi-session-contract-v1.md`](../../../../docs/csi-session-contract-v1.md)
(contract 1.0.0), Section 2 "Packet metrics" and Section 3. It also compares the result with
NFR-09: each receiver delivers at least 80 packets/s with packet loss below 5 %.

| File | Purpose |
|---|---|
| `packet_metrics.py` | `ReceiverMetrics` (one receiver, live or offline), `SessionMetrics` (all receivers, usable as the reader's callbacks), `summarize_capture` (replays a capture directory), `compare_nfr09`. |
| `__main__.py` | Command line. |

## Method

Input per receiver, in arrival order: accepted records (sender `id`, `host_monotonic_ns`) and the
events of `events.ndjson`.

- **Runs.** Sender ids are split into contiguous runs. A new run starts when:
  - the `id` is lower than the previous one: the sender restarted (counted in `sender_restarts`)
    or, from the top of the uint32 range to near 0, the counter wrapped (not counted as a restart);
  - the first record arrives after `receiver_disconnected` / `receiver_reconnected`.
- **Loss.** Per run, `expected = last_id - first_id + 1` and `missing = expected - unique ids`.
  The session values are the sums over all runs, and
  `estimated_loss_percent = 100 * missing_ids / expected_ids`. Ids that fall between two runs, for
  example while the sender was off or the receiver was unplugged, are not ordinary loss and are not
  counted. Ids before the first or after the last accepted record are not counted either.
- **Counter resets.** After a sender restart the first `id` may be above 0 (it was 1 in the GES-68
  check). A restarted run is measured like any other run, so loss inside it still counts.
- **Duplicates.** The same `id` again right after itself is counted in `duplicates` and adds
  nothing to `unique_sender_ids`. A lower `id` is always treated as a restart, as the contract
  says, even if it was seen earlier: a restarted sender counts again from 0 or 1.
- **Malformed lines.** Each `malformed_record` event adds 1 to `malformed_lines`. Its id never
  enters the run, so a sender packet seen only on a malformed line counts as missing.
- **Disconnects.** Each disconnect adds `{disconnected_utc, reconnected_utc, duration_s}` to
  `disconnect_intervals`. `reconnected_utc` and `duration_s` are `null` while the receiver is still
  away. The duration uses the host monotonic clock.
- **Rate.** `measured_packet_rate_hz = accepted_records / rate_interval_s`. The interval is the one
  the caller states (for example the capture duration or the signal-check window). Without it, the
  span between the first and last accepted record is used. The configured 100 packets/s is never
  used as the expected rate.
- **Not measured.** A receiver with no records has `null` rate and loss, and NFR-09 is reported as
  "not measured", never as met.

`provisional` is `true` by default, as contract 1.0.0 requires until the GES-68 hardware check is
recorded in the contract (pull request #7); pass `provisional=False` (`--final`) after that.

## Use

Offline, on a capture directory written by the reader (`receivers/*.ndjson`, `events.ndjson`):

```
# Windows (PowerShell), from the repository root
$env:PYTHONPATH="src"
py -m gestura.acquisition.metrics data/captures/e2e-1rx
py -m gestura.acquisition.metrics data/sessions/<dir> --interval-s 600 --write-session
```

It prints one line per receiver with the NFR-09 verdict, then the `packet_metrics` JSON.
`--write-session` replaces `packet_metrics` in an existing `session.json` and leaves the other keys
as they are.

Live, together with the reader:

```python
from gestura.acquisition.metrics import SessionMetrics, compare_nfr09
from gestura.acquisition.reader.reader import CaptureSession, ReceiverSpec

metrics = SessionMetrics(["rx1"])
session = CaptureSession(out_dir, [ReceiverSpec("rx1", "B2", "5C37270507")],
                         on_record=metrics.on_record, on_event=metrics.on_event)
...
pm = metrics.packet_metrics(interval_s=capture_seconds)
compare_nfr09(pm["rx1"])   # {"rate_ok": ..., "loss_ok": ..., "verdict": "met" | "not met" | "not measured"}
```

For the signal check of the recording flow (GES-71), feed a fresh `SessionMetrics` during the
check window and call `packet_metrics(interval_s=window)`.

## Tests

```
py -m pytest tests/metrics
```

They cover the GES-67 fixture (the result equals `packet_metrics` in `ges67_session_v1.json`),
normal reception, missing ids, duplicates, sender restarts (also during a disconnect), the uint32
wrap, negative ids from older firmware, malformed lines, disconnects with and without reconnect,
two receivers in one capture directory, the NFR-09 comparison and the command line.

## Interpretations to confirm

- A receiver restart (`receiver_restarted`) does not start a new run: the contract excludes only
  sender restarts, disconnects and wraps. Ids lost while the receiver reboots (about 0.35 s in the
  GES-68 check) therefore count as loss.
- `receiver_stalled` / `receiver_resumed` (proposed in GES-69, not in contract 1.0.0) do not
  change the calculation. A stall caused by a sender restart already starts a new run through the
  lower `id`.
