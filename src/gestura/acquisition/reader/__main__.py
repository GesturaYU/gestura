"""Command line for the host reader.

    python -m gestura.acquisition.reader --list-ports
    python -m gestura.acquisition.reader --config receivers.json --receiver rx1=B2 \
        [--receiver rx2=B3] --out data/captures/<name> [--duration 60]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .config import load_host_config, receiver_specs
from .ports import describe_ports
from .reader import CaptureSession, SessionStartError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gestura.acquisition.reader",
                                     description="Read CSI from one or two USB receivers.")
    parser.add_argument("--list-ports", action="store_true",
                        help="list serial ports with their USB serial numbers and exit")
    parser.add_argument("--config", help="host configuration JSON (board label -> USB serial)")
    parser.add_argument("--receiver", action="append", default=[], metavar="ID=BOARD",
                        help="receiver to read, e.g. rx1=B2; give once or twice")
    parser.add_argument("--out", help="new output directory (must not contain earlier output)")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--stall-timeout", type=float, default=2.0,
                        help="seconds without CSI lines before a receiver is reported stalled")
    args = parser.parse_args(argv)

    if args.list_ports:
        print(json.dumps(describe_ports(), indent=2))
        return 0
    if not (args.config and args.receiver and args.out):
        parser.error("--config, --receiver and --out are required (or use --list-ports)")

    boards, link = load_host_config(args.config)
    specs = receiver_specs(args.receiver, boards)

    def show_event(event: dict) -> None:
        detail = {k: v for k, v in event.items()
                  if k not in ("host_time_utc", "host_monotonic_ns", "receiver_id", "kind",
                               "raw_line")}
        print(f"[event] {event['receiver_id']} {event['kind']} {detail}", flush=True)

    session = CaptureSession(args.out, specs, link, stall_timeout_s=args.stall_timeout,
                             on_event=show_event)
    try:
        session.start()
    except SessionStartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"reading {', '.join(r['receiver_id'] + '@' + r['transport_port'] for r in session.receivers_info())}"
          f" -> {args.out} (Ctrl+C to stop)", flush=True)
    started = time.monotonic()
    try:
        while args.duration is None or time.monotonic() - started < args.duration:
            time.sleep(min(2.0, args.duration or 2.0))
            print("  " + " | ".join(
                f"{s.receiver_id} {s.state} records={s.accepted_records} "
                f"malformed={s.malformed_lines} last_id={s.last_id}" for s in session.status()),
                flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
    print(json.dumps({"receivers": session.receivers_info(),
                      "final_status": [vars(s) for s in session.status()]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
