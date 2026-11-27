"""Write synthetic receiver output to a serial port, for trying the reader without boards.

Pair it with a virtual serial port pair: on Windows com0com (e.g. COM20 <-> COM21), on Linux or
macOS `socat -d -d pty,raw,echo=0 pty,raw,echo=0`. Run the fake receiver on one end and point
the reader at the other. A virtual port usually reports no USB serial number, so the reader's
--config cannot find it; use the virtual-port test (tests/reader/test_serial_port.py) or a
short script with a custom resolver for that. Synthetic output only; never use it as data.

    python -m gestura.acquisition.reader.fake_receiver COM20 --rate 90 --seconds 30
"""
from __future__ import annotations

import argparse
import random
import time

HEADER = ("type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,"
          "stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,"
          "ant,sig_len,rx_state,len,first_word,data")


def synthetic_line(seq: int, timestamp_us: int, length: int = 256, rng=random) -> str:
    data = ",".join(str(rng.randint(-20, 20)) for _ in range(length))
    return (f"CSI_DATA,{seq},1a:00:00:00:00:00,{rng.randint(-60, -40)},11,1,0,0,1,1,0,0,0,0,"
            f"-96,0,11,0,{timestamp_us % 2 ** 32},0,47,0,{length},0,\"[{data}]\"")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port")
    parser.add_argument("--rate", type=float, default=90.0, help="records per second")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--loss", type=float, default=0.01, help="fraction of ids skipped")
    args = parser.parse_args(argv)

    import serial

    with serial.Serial(args.port, 921600) as ser:
        ser.write(b"I (612) csi_recv_ab: ================ CSI RECV ================\n")
        ser.write((HEADER + "\n").encode())
        start, seq = time.monotonic(), 0
        while time.monotonic() - start < args.seconds:
            seq += 1
            if random.random() < args.loss:
                continue
            ser.write((synthetic_line(seq, int((time.monotonic() - start) * 1e6)) + "\n").encode())
            time.sleep(1 / args.rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
