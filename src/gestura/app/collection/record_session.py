"""Record one cued-trial session (GES-71).

Dry run without boards (test data, written under data/dry_runs/, ignored by git):

    python -m gestura.app.collection.record_session --dry-run --participant P1 --room R1 \
        --room-type campus --other-people 1 --recorder-in-room

Add --auto to answer every prompt automatically and skip the waiting (a fast check of the
files). Recording from real receivers needs the host reader (GES-69) and is added once it is
merged.
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

from gestura.app.collection.alignment import check_alignment
from gestura.app.collection.flow import (AutoUI, ConsoleUI, FakeClock, FlowConfig, FlowError,
                                         RealClock, run_signal_check, run_trials, wait_for_warmup)
from gestura.app.collection.session_files import (new_session, session_dir, to_rfc3339,
                                                  write_session_json, write_trials_csv)
from gestura.app.collection.sources import SyntheticSource
from gestura.app.collection.trial_plan import make_trial_plan

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
DRY_RUN_ROOT = os.path.join(REPO_ROOT, "data", "dry_runs")
SYNTHETIC_RATE_HZ = 90.0  # what GES-68 measured on the real boards

SYNTHETIC_SETUP = {
    "sender": {"board_id": "B1", "firmware_commit": "synthetic", "channel": 11,
               "bandwidth_mhz": 20, "configured_packet_rate_hz": 100},
    "receivers": [{"receiver_id": "rx1", "board_id": "B2", "usb_serial_number": "SYNTHETIC-B2",
                   "transport_port": "synthetic", "firmware_commit": "synthetic", "channel": 11,
                   "bandwidth_mhz": 20, "gain_setting": "forced_after_100_packets"}],
    "placement": {"description": "dry run, no boards", "sender_height_m": 0.75,
                  "receivers": {"rx1": {"distance_from_sender_m": 1.0, "height_m": 0.75,
                                        "antenna_orientation": "facing_sender"}}},
}


def tool_version():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return "unknown"


def record(args, clock, ui, beep, cfg, plan=None):
    """Run one session and return (session folder, session dict, trials, packet events)."""
    session = new_session(clock.utc_now(), args.participant, args.room, args.room_type,
                          args.room_role, SYNTHETIC_SETUP, args.other_people,
                          args.recorder_in_room, f"dry-run {tool_version()}")
    folder = session_dir(args.out, session)
    os.makedirs(folder, exist_ok=False)
    json_path = os.path.join(folder, "session.json")
    trials_path = os.path.join(folder, "trials.csv")
    write_session_json(json_path, session)

    source = SyntheticSource([r["receiver_id"] for r in session["receivers"]], clock,
                             rate_hz=SYNTHETIC_RATE_HZ)
    log, trials = [], []
    source.start()
    try:
        ui.show("Waiting for the receiver gain warm-up ...")
        wait_for_warmup(source, clock, cfg, log)
        ui.show(f"Signal check ({cfg.signal_check_s:g} s): keep still, hands outside the line.")
        session["signal_check"] = run_signal_check(source, clock, cfg, log)
        write_session_json(json_path, session)
        if not session["signal_check"]["passed"]:
            raise FlowError(f"Signal check failed: {session['signal_check']['receivers']}")
        ui.show("Signal check passed. Trials start now.")
        plan = plan or make_trial_plan()
        trials = run_trials(plan, session["session_id"], source, clock, ui, beep, cfg, log,
                            on_trial=lambda done: write_trials_csv(trials_path, done))
    finally:
        source.stop()
        session["end_time_utc"] = to_rfc3339(clock.utc_now())
        write_session_json(json_path, session)
    return folder, session, trials, log


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="use synthetic receivers (test data)")
    p.add_argument("--auto", action="store_true", help="no prompts, no waiting (dry run only)")
    p.add_argument("--participant", required=True, help="P1-P5")
    p.add_argument("--room", required=True, help="R1, R2, ... from the private room list")
    p.add_argument("--room-type", required=True, choices=["residential", "campus"])
    p.add_argument("--room-role", default="dataset", choices=["dataset", "final_demo"])
    p.add_argument("--other-people", type=int, required=True,
                   help="people in the room besides the participant, recorder included")
    p.add_argument("--recorder-in-room", action="store_true")
    p.add_argument("--out", default=DRY_RUN_ROOT, help="dataset root (default: data/dry_runs)")
    args = p.parse_args(argv)

    if not args.dry_run:
        sys.exit("Recording from real receivers needs the GES-69 host reader. Use --dry-run until it is merged.")

    cfg = FlowConfig()
    if args.auto:
        # Fast run: the fake clock starts at the real time, so session IDs stay unique.
        clock = FakeClock(start_utc=datetime.now(timezone.utc))
        ui, beep = AutoUI(quiet=True), (lambda f, ms: None)
    else:
        from gestura.acquisition.bip import beep
        clock, ui = RealClock(), ConsoleUI()
    try:
        folder, session, trials, log = record(args, clock, ui, beep, cfg)
    except (FlowError, ValueError) as e:
        sys.exit(f"Stopped: {e}")
    except KeyboardInterrupt:
        sys.exit("Stopped by the recorder; the trials so far are kept.")

    problems = check_alignment(trials, log, [r["receiver_id"] for r in session["receivers"]],
                               SYNTHETIC_RATE_HZ)
    print(f"\nDRY RUN (test data, not a participant recording): {folder}")
    print(f"{len(trials)} trials, {sum(t.failed for t in trials)} marked failed.")
    print("Label/timestamp alignment: " + ("OK" if not problems else "PROBLEMS"))
    for line in problems:
        print("  " + line)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
