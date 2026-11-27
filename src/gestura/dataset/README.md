# Session validation and the private dataset layout (GES-72)

`validate.py` checks recorded sessions against the session contract,
[`docs/csi-session-contract-v1.md`](../../../docs/csi-session-contract-v1.md) (schema 1.0.0),
Sections 2 and 3. It reports every problem with the file, the line or field, and what to fix.

## Private dataset layout

The layout is defined in contract Sections 3 and 4 and in the
[recording procedure](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form)
(GES-73), Section 8. In short:

```text
Gestura CSI Recordings/              # coded session data only (team OneDrive)
  sessions/<YYYY-MM-DD>_<session_id>/
    session.json
    trials.csv                     # cued_trials sessions only
    receivers/rx1.ndjson           # one file per receiver
    receivers/rx2.ndjson
    events.ndjson

Gestura Signed Consent/              # separate folder, never inside the one above
  signed forms and written confirmations
  participant list (code -> name), room list (room_id -> description, final demo room)
```

- Session files use only codes: `P1`–`P5` for participants and `R1`, `R2`, … for rooms. Names, room
  descriptions, images, video and audio are never stored with the recordings.
- Consent forms and the two private lists stay in `Gestura Signed Consent`. The validator fails a
  session directory that contains any other file, and warns about files next to `sessions/`.
- The public repository holds only synthetic fixtures (`tests/fixtures/`) and documentation. Local
  recordings, dry runs and the host configuration live under `data/`, which git ignores.

## Run

From the repository root, with `src` on the Python path (`$env:PYTHONPATH="src"` in PowerShell,
`PYTHONPATH=src` in front of the command on macOS and Linux; on Windows use `py` for `python`):

```
python -m gestura.dataset.validate "<path to Gestura CSI Recordings>"     # every session
python -m gestura.dataset.validate data/dry_runs                          # recording-flow dry runs
python -m gestura.dataset.validate <dataset root>/sessions/<session dir>  # one session
python -m gestura.dataset.validate <path> --quiet                         # errors and summary only
```

The path can be a session directory, a `sessions/` folder or a dataset root that contains
`sessions/`. Exit code 0 means no errors, 1 means at least one error, 2 means the path is not a
session or a dataset.

| Level | Meaning |
|---|---|
| `ERROR` | The session does not follow the contract and must be fixed before it is used. |
| `WARNING` | The session follows the contract, but something needs a look: for example an interrupted session with fewer trials, a trial during a receiver disconnect, or packet metrics that are still provisional. |
| `INFO` | For information, for example that a dry run writes no receiver files. |

At most 20 findings per file and level are listed; the rest are counted in one line.

## What is checked

| File | Checks |
|---|---|
| Session directory | Named `<recording_date>_<session_id>`; holds only the files below (OS files such as `.DS_Store` are ignored); `trials.csv` present for `cued_trials` and absent for `continuous_no_gesture`; one `receivers/<receiver_id>.ndjson` per listed receiver and `events.ndjson`. |
| `session.json` | Every key of Section 3; unknown keys are warned about. `schema_version` 1.0.0. `session_id` matches the UTC start time and `recording_date` its Europe/Istanbul date. Start and end are RFC 3339 UTC, end after start (a null end means the session was not finalized). `participant_code` P1–P5 and `room_id` R&lt;n&gt; (the value is not printed, since it may be a name). Allowed values of `room_type`, `room_role`, `session_type`. Sender and one or two receivers with all keys, unique receiver ids and USB serial numbers, the same channel and bandwidth. Placement per receiver. The people fields agree. A signal check that passed. `packet_metrics` per receiver: `provisional` true is accepted (warning, info for a dry run); when false, the values are complete and `missing_ids = expected_ids - unique_sender_ids`. |
| `trials.csv` | The header of Section 3 in order; `session_id` of the session; `trial_id` 1, 2, 3, …; labels `push`, `slow_wave`, `fast_wave`, `no_gesture`; RFC 3339 cue times; cue end after cue start; windows in order without overlap; `failed` true or false with a `failed_reason` only for failed trials. Class counts other than 10/10/10/20 are warned about. |
| `receivers/<id>.ndjson` | One JSON record per line with the host fields and the 25 wire fields; `receiver_id` of the file; `host_monotonic_ns` never going back; `raw_line` parsed again with the GES-69 parser gives the same fields. Records with another channel or bandwidth than `session.json` are warned about. |
| `events.ndjson` | Each event has the common fields, a listed receiver and one of the kinds `malformed_record`, `receiver_restarted`, `config_mismatch`, `receiver_disconnected`, `receiver_reconnected`, `receiver_stalled`, `receiver_resumed`, with its own fields; `malformed_record` reasons are the seven reason codes. `receiver_stalled` / `receiver_resumed` come from the GES-69 reader and are added to the contract's events table by GES-70. |
| Labels against packets | For trials that did not fail: the cue window must lie inside each receiver's records (otherwise the label cannot be matched, an error). A window with fewer than 80 % of 80 packets/s (NFR-09), or during a receiver disconnect, is warned about. |
| Dataset root | Duplicate `session_id`s; files directly in `sessions/` (error) or next to it (warning). |

**Dry runs.** A session whose `collection_tool_version` starts with `dry-run` comes from the
recording flow with synthetic receivers (GES-71). It has no receiver or event files, so these are
reported as info instead of errors.

Not checked here: recomputing `packet_metrics` from the records (the GES-70 metrics tool does
that) and who the recorder was (open question 4 in `docs/evaluation-splits-v1.md`).

## Tests

```
python -m pytest tests/dataset
```

The tests build a complete synthetic session (receiver records from the GES-69 fake receiver,
trials and `session.json` from the GES-71 writers) and check that it passes, that a recording-flow
dry run passes, that each kind of deliberate fault fails with an error naming its file and field,
and the warnings and exit codes.
