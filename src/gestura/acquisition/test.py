"""Menu for the CSI feasibility tests: finds the receiver port, runs the recording, then the analysis.

Calls check_signal.py and record_*.py from this folder and analyze_*.py from ../evaluation, and
saves each printout next to its recording under data/recordings/ (ignored by git), so nothing
has to be copied by hand.

Usage: python src/gestura/acquisition/test.py
"""
import os
import subprocess
import sys
import time

from serial.tools import list_ports

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(os.path.dirname(HERE), "evaluation")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT_DIR = os.path.join(REPO_ROOT, "data", "recordings")


def pick_port():
    ports = [
        p for p in list_ports.comports()
        if "bluetooth" not in (p.device + (p.description or "") + (p.hwid or "")).lower()
        and "debug-console" not in p.device
    ]
    if not ports:
        sys.exit("No board found. Is the receiver (A) plugged into the computer's own USB port, UART/COM side?")
    if len(ports) == 1:
        print(f"Port: {ports[0].device}  ({ports[0].description})")
        return ports[0].device
    for i, p in enumerate(ports, 1):
        print(f"  {i}) {p.device}  {p.description}")
    return ports[int(input("Which one is the receiver (A)? Number: ")) - 1].device


def run(script, *args, save_to=None):
    cmd = [sys.executable, script, *args]
    if save_to is None:
        return subprocess.call(cmd)
    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    text = res.stdout + res.stderr
    print(text)
    with open(save_to, "w", encoding="utf-8") as f:
        f.write(text)
    print("Saved:", save_to)
    return res.returncode


def record_and_analyze(port, record, analyze, kind):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{kind}-{time.strftime('%m%d-%H%M')}.txt")
    input("Press Enter when ready. The beeps start after 10 seconds. ")
    if run(os.path.join(HERE, record), port, path) != 0:
        print("The recording failed.")
        return
    run(os.path.join(EVAL_DIR, analyze), path, save_to=path + ".analysis.txt")


def main():
    port = pick_port()
    while True:
        print("\n1) Signal check (30 s)\n2) Rhythm test (2 min)\n3) Motion / no motion test (1.5 min)\n4) Exit")
        choice = input("Choice: ").strip()
        if choice == "1":
            os.makedirs(OUT_DIR, exist_ok=True)
            run(
                os.path.join(HERE, "check_signal.py"),
                port,
                save_to=os.path.join(OUT_DIR, f"signal-{time.strftime('%m%d-%H%M')}.txt"),
            )
        elif choice == "2":
            record_and_analyze(port, "record_rhythm.py", "analyze_rhythm.py", "rhythm")
        elif choice == "3":
            record_and_analyze(port, "record_protocol.py", "analyze_protocol.py", "motion")
        elif choice == "4":
            break
    print("Recordings:", OUT_DIR)


if __name__ == "__main__":
    main()
