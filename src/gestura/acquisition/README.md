# CSI feasibility test (GES-36)

Scripts to record Wi-Fi CSI between two ESP32-S3 boards and check whether a hand moving through the line between them leaves a trace. They repeat the 17 October feasibility test with another team member on another day. Record whatever comes out as it is; do not try to make the result look better.

- `src/gestura/acquisition/`: `test.py` (menu), `check_signal.py`, `record_protocol.py`, `record_rhythm.py`, `bip.py` (beeps on Windows and macOS).
- `src/gestura/evaluation/`: `analyze_protocol.py`, `analyze_rhythm.py`.
- Recordings and analysis printouts are written to `data/recordings/`. That folder is ignored by git; never commit recordings.

## Setup (once)

Python 3 is required. From the repository root:

Windows:

```
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Place the boards (every attempt)

- Put the two boards on the same table, about 1 m apart, at the same height, lying flat, antennas (the end opposite the USB connector) facing each other. The cable must not lift a board; tape it down if needed.
- Receiver (A): plug into the computer's own USB port, not a hub, on the board's "UART/COM" connector.
- Sender (G): power only (charger or any other USB port). Wait 15 s after plugging it in.

## Run the test (every attempt)

From the repository root:

Windows: `.venv\Scripts\python.exe src\gestura\acquisition\test.py`

macOS: `.venv/bin/python src/gestura/acquisition/test.py`

The program finds the receiver's port and shows a menu:

```
1) Signal check (30 s)
2) Rhythm test (2 min)
3) Motion / no motion test (1.5 min)
4) Exit
```

Run 1 first, then 2 (twice if possible), then 3. You return to the menu after each choice. Keep the sound on. Keep your body still; only the hand and arm move.

1. **Signal check.** Wait 5 s, then on every beep pass your hand once up and down through the exact middle between the two boards. At the end `VERDICT: trace visible` means you can continue. If it does not appear, or the packet rate is below 30 pkt/s, lay the boards flat, wait 15 s and try again.
2. **Rhythm test.** Press Enter, wait 10 s, then four rounds of 10 s phases:
   - 1 long low beep = STILL: hand outside the line, do not move.
   - 2 medium beeps = SLOW: hand up and down through the middle, about once per second.
   - 3 short high beeps = FAST: same motion, about three times per second.

   Three long beeps mean it is finished; the analysis runs automatically. If you run it a second time, rest 5 minutes in between.
3. **Motion / no motion test.** Press Enter, wait 10 s, then six phases of 15 s:
   - 1 long low beep = STILL: hand outside the line.
   - 2 short high beeps = HAND: pass the hand up and down through the middle.

   Three long beeps mean it is finished; the analysis runs automatically.

If a recording is interrupted (someone walked in, you misunderstood a cue), ignore that recording and run it again.

## When you are done

Send the contents of `data/recordings/` (recordings and analysis printouts) to whoever writes up GES-36, with a short message: where you did it (room), whether anyone else was in the room, Windows or macOS, and any problem you had.

## Troubleshooting

- "No board found": try a different data cable and plug A directly into the computer.
- The port will not open: another program (Arduino IDE, a serial monitor) is holding it; close it.
- No beep: check the sound settings.
