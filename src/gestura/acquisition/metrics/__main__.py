"""Command line: packet metrics of a capture directory, compared with NFR-09.

    python -m gestura.acquisition.metrics data/captures/e2e-1rx
    python -m gestura.acquisition.metrics data/sessions/<dir> --interval-s 60 --write-session
"""
import argparse
import json
import sys
from pathlib import Path

from .packet_metrics import NFR09_MAX_LOSS_PERCENT, NFR09_MIN_RATE_HZ, compare_nfr09, summarize_capture


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m gestura.acquisition.metrics",
                                description="Packet rate and sequence-based loss per receiver (GES-70).")
    p.add_argument("capture_dir", type=Path, help="directory with receivers/*.ndjson and events.ndjson")
    p.add_argument("--interval-s", type=float, default=None,
                   help="interval the rate is measured over (default: first to last accepted record)")
    p.add_argument("--final", action="store_true",
                   help="mark the metrics as not provisional")
    p.add_argument("--write-session", action="store_true",
                   help="write packet_metrics into <capture_dir>/session.json (must already exist)")
    args = p.parse_args(argv)

    try:
        metrics = summarize_capture(args.capture_dir, args.interval_s, provisional=not args.final)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"NFR-09 target: at least {NFR09_MIN_RATE_HZ:g} packets/s and loss below {NFR09_MAX_LOSS_PERCENT:g} %")
    for rid, m in metrics.items():
        nfr = compare_nfr09(m)
        print(f"{rid}: {m['accepted_records']} records over {m['rate_interval_s']} s, "
              f"rate {m['measured_packet_rate_hz']} packets/s, loss {m['estimated_loss_percent']} % "
              f"({m['missing_ids']} of {m['expected_ids']} ids missing), duplicates {m['duplicates']}, "
              f"malformed {m['malformed_lines']}, sender restarts {m['sender_restarts']}, "
              f"disconnects {len(m['disconnect_intervals'])} -> NFR-09 {nfr['verdict']}")
    print(json.dumps(metrics, indent=2))

    if args.write_session:
        path = args.capture_dir / "session.json"
        if not path.exists():
            print(f"error: {path} does not exist; it is created by the recording flow", file=sys.stderr)
            return 2
        session = json.loads(path.read_text(encoding="utf-8"))
        session["packet_metrics"] = metrics
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        print(f"packet_metrics written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
