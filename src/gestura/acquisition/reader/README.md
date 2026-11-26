# Host reader for one or two CSI receivers (GES-69)

Reads `CSI_DATA` lines from one or two ESP32-S3 receivers over USB serial, validates them against
[`docs/csi-session-contract-v1.md`](../../../../docs/csi-session-contract-v1.md) (contract 1.0.0)
and writes them into a session directory without mixing the streams.

Out of scope here: packet rate and loss summaries (GES-70), the scripted recording flow and
`session.json` (GES-71), and metadata validation (GES-72). Those tools use this reader as a library.

## Files

| File | Purpose |
|---|---|
| `wire.py` | Parses and validates one line: the 25 fields, the seven reason codes in contract order, negative `id`/`local_timestamp` from older firmware converted to unsigned. No I/O. |
| `reader.py` | `CaptureSession`: one thread per receiver, identity by USB serial number, output files, events, status. |
| `ports.py` | Serial port discovery by USB serial number; opens ports at 921600 8N1 without toggling DTR/RTS (so opening a port does not reset the board). |
| `config.py`, `receivers.example.json` | Host configuration: board label -> USB serial number, expected link settings. |
| `__main__.py` | Command line. |
| `fake_receiver.py` | Writes synthetic receiver output to a serial port, for trying things without boards. |

## Setup

From the repository root, as in `src/gestura/acquisition/README.md`, then install the test runner:

```
# Windows
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` includes `pytest`. Use Python 3.13 or newer on Windows: from 3.13,
`time.monotonic_ns()` on Windows has microsecond resolution instead of about 15.6 ms, and
`host_monotonic_ns` is what trial labels are matched on.

## Run the tests (no boards needed)

```
# Windows
.venv\Scripts\python.exe -m pytest tests/reader

# macOS / Linux
.venv/bin/python -m pytest tests/reader
```

`pytest.ini` puts `src/` on the import path. The tests cover:

- `test_wire.py`: the GES-67 fixture (`tests/fixtures/ges67_wire_v1.csv`) gives exactly
  `ges67_expected_v1.json`; every reason code; the check order; CRLF; negative unsigned values.
- `test_reader.py` (fake ports): the fixture through one receiver; lines split across reads; two
  receivers with interleaved half-lines; missing, duplicate and restarted sequence numbers kept as
  received; receiver restart; configuration mismatch; a stalled receiver and an unplugged
  receiver (reported, the other stream continues, reconnect on a new port name); session start
  checks; existing output never overwritten.
- `test_serial_port.py` (Linux/macOS only, skipped on Windows): two receivers through real
  pyserial ports, using pseudo-terminals instead of boards.

## Run the reader

1. Find the USB serial number of each receiver (plug in one board at a time if unsure):

   ```
   python -m gestura.acquisition.reader --list-ports
   ```

2. Copy `receivers.example.json` to `data/receivers.json` (ignored by git) and fill in the
   serial number of each board label.

3. Read one or two receivers into a new directory:

   ```
   python -m gestura.acquisition.reader --config data/receivers.json --receiver rx1=B2 --out data/captures/e2e-1rx --duration 60
   python -m gestura.acquisition.reader --config data/receivers.json --receiver rx1=B2 --receiver rx2=B3 --out data/captures/e2e-2rx --duration 120
   ```

   Run from the repository root with `src` on the path: on Windows
   `set PYTHONPATH=src` (cmd) or `$env:PYTHONPATH="src"` (PowerShell); on macOS/Linux prefix the
   command with `PYTHONPATH=src`. The reader prints each receiver's state every 2 s and every event
   as it happens. Ctrl+C stops it. It refuses to start if a configured receiver is not connected,
   or if the output directory already contains reader output.

## Output

```
<out>/
  receivers/rx1.ndjson
  receivers/rx2.ndjson   # only with a second receiver
  events.ndjson
```

Each record line: `schema_version`, `receiver_id`, `host_time_utc`, `host_monotonic_ns`, the 25
wire fields in contract order (`data` as an integer array) and `raw_line`. Sender ids are never
renumbered and duplicates are never removed; loss is computed later from them (GES-70).

Events (`events.ndjson`): `malformed_record`, `receiver_restarted`, `config_mismatch`,
`receiver_disconnected` and `receiver_reconnected` as in contract Section 2, plus two kinds that
are **not in contract 1.0.0 yet**:

- `receiver_stalled` (`seconds_without_records`, `transport_port`): the port is still open but no
  `CSI_DATA` line arrived for `--stall-timeout` seconds (default 2). Log lines do not count.
- `receiver_resumed` (`stalled_for_s`): `CSI_DATA` lines arrive again.

GES-69 requires stalls to be reported; the contract only names disconnects. These two kinds need
to be added to the contract (and accepted by the GES-72 validator) before merging.

Interpretations to confirm with the contract owner:

- `receiver_restarted` is written for a header row after the first `CSI_DATA` line of the session.
  A header before any record (board booting as capture starts) only starts the warm-up count.
- `config_mismatch` carries `id` and `mismatches` (`{field: {expected, actual}}`). It is written
  when the mismatching values change, and again if they change after returning to the expected
  values.
- Lines read in one chunk share one receipt timestamp.
- After `receiver_reconnected`, the gain warm-up state is unknown (`warmup_records_remaining` is
  `None` in `status()`). In the board check, replugging a receiver restarted it (`local_timestamp`
  started again near 0) and its banner and header were printed before Windows reopened the port,
  so no `receiver_restarted` event was written. Downstream processing should treat the first 100
  records after a reconnect as possible warm-up records.

## Library use

```python
from gestura.acquisition.reader.reader import CaptureSession, ReceiverSpec

session = CaptureSession("data/sessions/2026-11-24_S20261124-120000",
                         [ReceiverSpec("rx1", "B2", "5C37270507")],
                         on_record=lambda r: ..., on_event=lambda e: ...)
session.check_receivers()   # raises SessionStartError if a serial number is missing
session.start()
session.status()            # per receiver: state, port, counts, last id, warm-up remaining
session.receivers_info()    # receiver_id, board_id, usb_serial_number, transport_port for session.json
session.stop()
```

Callbacks run on the reader threads; keep them short.

## End-to-end check with boards

Needs the GES-68 firmware on one sender and two receivers. Record the results in the GES-69
evidence (and the GES-75 test plan):

| Step | Expected |
|---|---|
| `--list-ports` with both receivers | Each receiver shows a distinct USB serial number |
| One receiver, 60 s | `rx1.ndjson` fills at about 90 records/s, increasing ids, `len` 256, no unexpected events |
| Two receivers, 120 s | Two files, each with its own `receiver_id`; equal ids appear in both (same sender packets) |
| Unplug rx2 for about 10 s during the two-receiver run, then plug it back | `receiver_disconnected` and `receiver_reconnected` for rx2; rx1 keeps growing throughout |
| Power off the sender for about 10 s | `receiver_stalled` then `receiver_resumed` on both receivers |

Record: date, who ran it, board labels and serial numbers, firmware commit, placement, OS and
Python version, and the pass/fail outcome of each step. Raw captures stay under `data/`, never in
the repository.
