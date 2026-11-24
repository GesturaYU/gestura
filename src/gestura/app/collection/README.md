# Cued-trial recording flow (GES-71)

Records one session of scripted trials: push, slow wave, fast wave and no gesture, with the label
and the cue times saved automatically. The files follow
[`docs/csi-session-contract-v1.md`](../../../../docs/csi-session-contract-v1.md) (Section 3) and the
[recording procedure](https://gestura-se4910.atlassian.net/wiki/spaces/GP/pages/23101469/Recording+Procedure+and+Consent+Form) (GES-73).

## What a session does

1. Creates `sessions/<YYYY-MM-DD>_<session_id>/session.json` before capture.
2. Waits for the gain warm-up: every receiver must send its header row and then 100 records.
3. Signal check (10 s): every receiver connected and at least 80 packets/s (NFR-09). The result is
   stored in `session.json`; the session stops if the check fails.
4. 50 trials in random order: 10 push, 10 slow wave, 10 fast wave, 20 no gesture. For each trial
   the class is shown, a high beep starts a 3 s cue window and a low beep ends it. The recorder then
   presses Enter (OK), or types `f` and picks a reason, or types `f1`-`f4` to give the reason at once
   (failed trials stay in the file with `failed=true`). Any other answer is asked again.
5. `trials.csv` is rewritten after every trial, so an interrupted session keeps its trials.
   `end_time_utc` is written at the end.

Labels are matched to packets by `host_monotonic_ns`; the cue times are taken just before each beep.

## Dry run (no boards, test data)

From the repository root, with `src` on the Python path:

Windows (PowerShell):

```
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m gestura.app.collection.record_session --dry-run --participant P1 --room R1 --room-type campus --other-people 1 --recorder-in-room
```

macOS:

```
PYTHONPATH=src .venv/bin/python -m gestura.app.collection.record_session --dry-run --participant P1 --room R1 --room-type campus --other-people 1 --recorder-in-room
```

This runs in real time with beeps and prompts (about 6 minutes). Add `--auto` for a run of a few
seconds with no prompts and no waiting. A dry run uses synthetic receivers at 90 packets/s (the rate
measured in GES-68), writes to `data/dry_runs/` (ignored by git), stores `dry-run <commit>` as
`collection_tool_version` and `synthetic` as the firmware commit, and prints the label/timestamp
alignment check at the end. Its output is test data, never a participant recording.

## Not yet included

- Real receivers: needs the host reader (GES-69). Without `--dry-run` the tool stops with a message.
- `packet_metrics` and loss < 5 % in the signal check: filled by GES-70; the keys are present with
  `null` values and `provisional: true`.
- `receivers/*.ndjson` and `events.ndjson`: written by the host reader (GES-69).
- Continuous `continuous_no_gesture` sessions (10 minutes, no trials).

## Choices to confirm in review

- Cue window 3 s for every class (slow wave lasts 2-3 s), 2 s lead-in, 2 s rest.
- Signal check: 10 s, every receiver connected and at least 80 packets/s.
- `trial_id` is a number from 1; failure reasons are `cue_missed`, `hand_not_through_line`,
  `wrong_gesture`, `other` (examples from the procedure, Section 7).

## Tests

```
pip install pytest
PYTHONPATH=src python -m pytest tests
```
