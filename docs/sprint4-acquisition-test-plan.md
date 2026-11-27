# Sprint 4 acquisition test plan and results

Jira: GES-75. Sprint 4 (16-29 November 2026). Contract: [`csi-session-contract-v1.md`](csi-session-contract-v1.md) (1.0.0).

This plan lists the repeatable checks for the parts of the acquisition pipeline built in Sprint 4:
the ESP32-S3 firmware (GES-68), the USB host reader (GES-69), packet rate and loss (GES-70), the
cued-trial recording flow (GES-71) and session metadata validation (GES-72). Each check names its
input or setup, the expected outcome, where the evidence is, and whether it uses synthetic data or
physical boards. The results section records what was actually run.

Requirement traces follow the Statement of Work traces in the Jira issues: GES-68 FR-01 and FR-02,
GES-69 FR-03 and FR-04, GES-70 FR-05, FR-06 and NFR-09, GES-71 FR-18 and FR-19, GES-72 (and the
GES-67 session schema) FR-20 and FR-21. Together they are the scope of GES-75 (FR-01 to FR-06 and
FR-18 to FR-21).

Rules used here:

- Outcomes are **pass**, **fail**, **blocked** (the component or the hardware it needs is not
  available yet) or **not run**.
- A hardware check passes only on a run with physical boards. Synthetic fixtures and fake ports
  can pass the synthetic part of a check, never the hardware part.
- A component that is not built is recorded as blocked, not as tested.
- Raw recordings, participant data and signed consent forms are never in this repository
  (contract Section 4; `data/*` is ignored by git). Only non-sensitive summaries are recorded here.

## Board configurations

| ID | Used in | Host | Boards |
|---|---|---|---|
| HW-W1 | GES-69 board check, GES-70 hardware result (Mert Aydul, 2026-11-26) | Windows, Python 3.12.10, pyserial 3.5 | Receiver: CH343 USB serial 5C37270507 on COM5, firmware `csi_recv_ab` 396636d-dirty (ESP-IDF v5.5.6). One sender on a USB charger, about 1 m away, antennas facing. |
| HW-M1 | GES-68 second-member check, GES-69 board check, GES-70 review (Sadıkhan Görmüş, 2026-11-26) | macOS (Apple Silicon), Python 3.9.6, pyserial 3.5 | Receiver B1: CH343 USB serial 5D2F052124, firmware 83af062. Sender G: USB serial 5D2F051820, firmware 83af062, power only. About 1 m apart. |
| HW-E1 | GES-68 hardware check (Efecan Cengiz, 2026-11-23) | Not recorded | One receiver and one sender about 1 m apart, both ESP32-S3; receiver CH343 USB serial 5C37270507. |

All configurations use channel 11, 20 MHz (HT20), ESP-NOW from sender MAC `1a:00:00:00:00:00` and
921600 baud on the receiver's CH343 port (firmware/README.md).

## Checks

| ID | Check | Issue / trace | Input or setup | Expected outcome | Type | Evidence |
|---|---|---|---|---|---|---|
| C1 | Fixed-channel CSI capture | GES-68; FR-01, FR-02 | Sender and receiver with the GES-68 firmware, 60-300 s reading | CSI_DATA lines at about 90/s; every record channel 11, bandwidth 0, secondary_channel 0, len 256, rx_state 0 | Hardware | GES-68 Jira comments; [PR #8 board check](https://github.com/GesturaYU/gestura/pull/8) |
| C2 | Wire record parsing | GES-67, GES-69; FR-03 | `tests/fixtures/ges67_wire_v1.csv` through the parser | Exactly `ges67_expected_v1.json`: 25 fields in contract order, header and log lines ignored | Synthetic | `tests/reader/test_wire.py` |
| C3 | Malformed records | GES-69; FR-03 | Synthetic lines for each reason code; on boards, lines cut when the reading stops or the cable is pulled | Each line gets the first failing reason code as a `malformed_record` event with `raw_line` and `id_if_parseable`; never a CSI sample | Synthetic + hardware | `tests/reader/test_wire.py`, `tests/reader/test_reader.py`; PR #8 board checks |
| C4 | Receiver identity | GES-69; FR-04 | `--list-ports` with the receiver connected; synthetic missing and duplicate serial numbers | Receiver found by its USB serial number; the session refuses to start if a configured serial number is missing or reported by more than one port | Synthetic + hardware | `tests/reader/test_reader.py`; PR #8 board checks |
| C5 | Receiver count: one and two receivers without mixing | GES-69; FR-04 | Synthetic: two fake ports with interleaved half-lines, two pseudo-terminals. Hardware: two receivers on one host | Two output files, each with only its own `receiver_id` and ids; no event caused by the other stream | Synthetic + hardware | `tests/reader/test_reader.py`, `tests/reader/test_serial_port.py` |
| C6 | Stall, disconnect and reconnect | GES-69; FR-04 | Sender unplugged about 10 s; receiver unplugged about 10 s and plugged back | `receiver_stalled` / `receiver_resumed` for the sender; `receiver_disconnected` / `receiver_reconnected` for the receiver; the other stream (if any) keeps reading | Synthetic + hardware | `tests/reader/test_reader.py`; PR #8 board checks |
| C7 | Sequence gaps, duplicates and sender restarts | GES-69, GES-70 | Synthetic gaps, duplicates and a restarted counter; on boards a sender restart | Sender ids kept exactly as received (no renumbering, no removed duplicates) | Synthetic + hardware | `tests/reader/test_reader.py`; PR #8 board checks |
| C8 | Packet rate and loss calculation | GES-70; FR-05, FR-06 | GES-70 metrics tool on synthetic fixtures and on HW-W1 / HW-M1 reader recordings | Loss from sequence gaps within runs; disconnects and sender restarts detected and not counted as loss; result equal to an independent count of the same file | Synthetic + hardware | [PR #10](https://github.com/GesturaYU/gestura/pull/10); GES-70 Jira comments |
| C9 | Observed rate and loss against NFR-09 | GES-70; NFR-09 | Undisturbed reading with one receiver | Values compared with the NFR-09 target (at least 80 packets/s, loss below 5 %); the target is not claimed unless measured | Hardware | GES-68, GES-70 Jira comments; PR #10 |
| C10 | Trial labels and timing | GES-71; FR-18, FR-19 | Synthetic: recording flow dry run (`--dry-run --auto`) and `tests/test_recording_flow.py`. Hardware: the same flow with a real receiver | 50 trials covering the four classes; every trial has its label and cue times; labels align with packets by `host_monotonic_ns` | Synthetic + hardware | `tests/test_recording_flow.py`, `tests/test_trial_plan.py`; PR #9; dry-run output in `data/dry_runs/` |
| C11 | Session files | GES-71; FR-20 | `tests/test_session_files.py`; dry-run session | `session.json` and `trials.csv` with the keys and columns of contract Section 3 and a schema version | Synthetic | `tests/test_session_files.py` |
| C12 | Required metadata validation | GES-72; FR-20, FR-21 | GES-72 validator on a valid and a deliberately invalid synthetic session | Valid session passes; invalid session fails with actionable errors | Synthetic | GES-72 (in progress) |
| C13 | Private data stays out of the repository | GES-67, GES-73 | `git check-ignore` on files under `data/`; review of `tests/fixtures/` | Recordings and configuration under `data/` are ignored; fixtures are synthetic | Repository check | `.gitignore`; this document |

## How to repeat the checks

From the repository root, with `src` on the Python path (`$env:PYTHONPATH="src"` in PowerShell,
`PYTHONPATH=src` in front of the command on macOS and Linux). Board configuration: put each
receiver's USB serial number in `data/receivers.json` (copy of
`src/gestura/acquisition/reader/receivers.example.json`; ignored by git).

| Checks | Command |
|---|---|
| C2-C7 (synthetic) | `python -m pytest tests/reader` |
| C8 (synthetic) | `python -m pytest tests/metrics` (on the GES-70 branch until PR #10 is merged) |
| C10, C11 (synthetic) | `python -m pytest tests/test_recording_flow.py tests/test_trial_plan.py tests/test_session_files.py` |
| C10 (dry run) | `python -m gestura.app.collection.record_session --dry-run --auto --participant P1 --room R1 --room-type campus --other-people 1 --recorder-in-room`; output in `data/dry_runs/` |
| All synthetic checks | `python -m pytest` |
| C1, C4 (boards) | `python -m gestura.acquisition.reader --list-ports` |
| C1, C3, C6, C7 (boards) | Undisturbed: `python -m gestura.acquisition.reader --config data/receivers.json --receiver rx1=<label> --out data/captures/<new name> --duration 60`. Faults: the same with `--duration 100`; unplug the sender for about 10 s at about 15 s, then the receiver for about 10 s at about 50 s. Steps in `src/gestura/acquisition/reader/README.md`. |
| C5 (boards) | As above with a second `--receiver rx2=<label>` and `--duration 120` |
| C8, C9 (boards) | `python -m gestura.acquisition.metrics data/captures/<recording>` on the recordings above |
| C13 | `git check-ignore -v data/receivers.json data/captures/<recording>/events.ndjson` |

Firmware build and flash for C1: `firmware/README.md`. On Windows use `py` for `python`.

## Results

| ID | Outcome | Date | Configuration | Result |
|---|---|---|---|---|
| C1 | pass | 2026-11-23, 2026-11-26 | HW-E1, HW-W1, HW-M1 | HW-E1: 300 s at 90.0 records/s, 1.06 % loss. HW-W1: 60 s, 5,463 records at 91.0 records/s. HW-M1: 60 s, 5,353 records at about 90/s, 1.6 % loss. Every record channel 11, bandwidth 0, len 256, rx_state 0; no task-watchdog lines. HW-W1 also showed that one of the borrowed sender boards sent no packets (receiver silent, `receiver_stalled` after 2 s); the board was set aside. |
| C2 | pass | 2026-11-27 | Windows, Python 3.12.10, main 854dfad | Fixture result equal to `ges67_expected_v1.json`; all reason-code tests pass. Full suite: 91 passed, 1 skipped (the pseudo-terminal test, which needs Linux or macOS). |
| C3 | pass | 2026-11-26, 2026-11-27 | Synthetic; HW-W1, HW-M1 | Every reason code tested. On boards, the line cut when the reading stopped (id 36625, HW-W1) and the line cut by pulling the receiver (id 2174, HW-W1; id 79398, HW-M1) were reported as `malformed_record` and not accepted. |
| C4 | pass | 2026-11-26, 2026-11-27 | Synthetic; HW-W1, HW-M1 | `--list-ports` found 5C37270507 (HW-W1) and 5D2F052124 (HW-M1); missing and duplicate serial numbers stop the session start in the tests. |
| C5 | synthetic: pass; hardware: blocked | 2026-11-26, 2026-11-27 | Synthetic; HW-W1 | Two receivers with interleaved half-lines and two pseudo-terminals pass (pseudo-terminal test on Linux and macOS; skipped on Windows). Hardware: only one receiver board was available, so two receivers were not read on boards. |
| C6 | pass | 2026-11-26 | HW-W1, HW-M1 | Sender unplugged: `receiver_stalled` 2.0 s after the last record, `receiver_resumed` after 12.5 s (HW-W1) and 13.4 s (HW-M1). Receiver unplugged: `receiver_disconnected`, `receiver_reconnected` after 10.7 s (HW-W1). On macOS `receiver_restarted` followed 0.35 s after the reconnect; on Windows the header was printed before the port was reopened, so the warm-up state is marked unknown instead (reader README). |
| C7 | pass | 2026-11-26 | Synthetic; HW-W1, HW-M1 | Sender restart: id went from 129253 to 0 and was kept as received (HW-W1); no duplicates in any board recording. |
| C8 | pass (PR #10 at 5c19f44, approved, not merged) | 2026-11-26, 2026-11-27 | Synthetic; HW-W1, HW-M1; GES-70 branch at 5c19f44 | HW-W1 undisturbed: 0 of 5,463 ids missing. HW-W1 with faults: 54 of 6,987 ids missing (0.77 %), sender restart and 10.7 s disconnect detected, the 979 ids during the disconnect not counted as loss. Both equal an independent count. HW-M1: 3.99 % undisturbed, 0.25 % with faults, also equal to an independent count (Sadıkhan Görmüş, review of 5c19f44). Re-run on 2026-11-27 at 5c19f44 (Windows, Python 3.12.10): `tests/metrics` 25 passed, and both HW-W1 recordings gave the same results as above (91.0 packets/s and 0 of 5,463 missing; 0.77 % with 54 of 6,987 missing, 1 sender restart, 1 disconnect of 10.735 s). |
| C9 | met (measured) | 2026-11-23, 2026-11-26 | HW-E1, HW-W1, HW-M1 | Undisturbed: 90.0 records/s and 1.06 % loss (HW-E1); 91.0 packets/s and 0 % loss (HW-W1); 87.4 packets/s and 3.99 % loss (HW-M1). All above 80 packets/s and below 5 % loss. The recording with deliberate faults (69.3 packets/s over the whole interval) is correctly flagged as an unreliable session and is not used for NFR-09. |
| C10 | synthetic: pass; hardware: blocked | 2026-11-26, 2026-11-27 | Synthetic: macOS (Sadıkhan Görmüş, PR #9 review), Windows (main 854dfad) | Dry run with synthetic receivers at 90 packets/s (`--dry-run --auto`, Windows, 2026-11-27): 50 trials, 10 push, 10 slow wave, 10 fast wave, 20 no gesture, 0 failed, every cue window 3.0 s, label/timestamp alignment OK; `tests/test_recording_flow.py` and `tests/test_trial_plan.py` pass. On macOS a dry run also gave 50 trials covering the four classes with alignment OK, and the cue-tone fix was confirmed afterwards (tones about 31 ms after the recorded cue time, a fixed offset). Test data only. Hardware: the recording flow is not yet connected to the host reader (merged on 2026-11-27), so no session with real receivers was recorded. |
| C11 | pass | 2026-11-27 | Windows, Python 3.12.10, main 854dfad | `tests/test_session_files.py` passes. The dry-run `session.json` has every key of contract Section 3, `schema_version` 1.0.0 and `collection_tool_version` `dry-run 854dfad`; `trials.csv` has the contract's columns in order and the same `session_id`. `packet_metrics` values are still null with `provisional: true` until GES-70 is merged, and a dry run writes no `receivers/*.ndjson` or `events.ndjson` (those come from the host reader). |
| C12 | blocked | 2026-11-27 | – | GES-72 validator is in progress. |
| C13 | pass | 2026-11-27 | Windows test computer, main 854dfad | `git check-ignore` confirms that `data/receivers.json`, the board recordings under `data/captures/` and the dry-run session under `data/dry_runs/` are ignored. `tests/fixtures/` contains only the synthetic GES-67 fixtures. |

## Problems found by these checks

| Found in | Problem | Resolution |
|---|---|---|
| C1, HW-W1 | One of the borrowed sender boards sent no packets; the receiver stayed silent | Board set aside; another sender used. To be checked by the board owner. |
| C5, macOS | The pseudo-terminal test failed on macOS: a macOS pseudo-terminal rejects 921600 baud | Fixed in PR #8 (a1bc68d): the test opens pseudo-terminals at 115200 |
| C4, review | A serial number reported by two ports would have been resolved to one of them, against contract Section 2 | Fixed in PR #8 (a1bc68d): the session refuses to start |
| C6, HW-W1 | On Windows a replugged receiver restarts and prints its header before the port is reopened, so no `receiver_restarted` event is written and warm-up records are not marked | PR #8 (f6c6e48) marks the warm-up state unknown after a reconnect; the Windows/macOS difference is documented in the reader README |
| C6, HW-M1 | On macOS opening the port resets the receiver, which the reader documentation said it does not | Documented in PR #8 (a1bc68d) |
| C10, macOS | Cue tones started 0.3-0.8 s after the recorded cue time (afplay) | Fixed in PR #9 (1a56a19): one open sounddevice stream, about 31 ms fixed offset |
| C9 | On Python 3.12 for Windows, `host_monotonic_ns` advances in 15 ms steps | Open item: use Python 3.13 or newer on Windows for cued sessions |

## Open items

- **Two receivers on boards (C5):** needs a second receiver board. One of the borrowed sender
  boards sent no packets in HW-W1 and could be reflashed as a receiver after checking with
  Efecan.
- **Recording flow with real receivers (C10):** needs the recording flow to use the host reader
  (GES-69, now on main) and the packet metrics (GES-70, PR #10).
- **Metadata validation (C12):** run when GES-72 is merged.
- **Stall events in the contract:** `receiver_stalled` and `receiver_resumed` are added to the
  contract's events table in PR #10 (c26825d); the GES-72 validator accepts them from there.
- **Timestamps on Windows:** Python 3.12 on Windows gives `host_monotonic_ns` in 15 ms steps. Use
  Python 3.13 or newer on Windows for cued sessions, where labels are matched by
  `host_monotonic_ns`.
