"""The real pyserial path, through a pseudo-terminal pair instead of a board (Linux and macOS)."""
import os
import sys

import pytest

from conftest import read_ndjson, wait_until
from gestura.acquisition.reader.fake_receiver import HEADER, synthetic_line
from gestura.acquisition.reader.ports import open_serial
from gestura.acquisition.reader.reader import CaptureSession, ReceiverSpec

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")


def make_pty():
    import tty

    master, slave = os.openpty()
    tty.setraw(slave)
    return master, os.ttyname(slave), slave


def test_two_receivers_over_real_serial_ports(tmp_path):
    m1, dev1, s1 = make_pty()
    m2, dev2, s2 = make_pty()
    serials = {"SER-B2": dev1, "SER-B3": dev2}
    session = CaptureSession(tmp_path / "S1",
                             [ReceiverSpec("rx1", "B2", "SER-B2"),
                              ReceiverSpec("rx2", "B3", "SER-B3")],
                             resolver=lambda: dict(serials), opener=open_serial)
    session.start()
    try:
        os.write(m1, (HEADER + "\n").encode())
        for seq in range(1, 31):
            os.write(m1, (synthetic_line(seq, seq * 11000) + "\n").encode())
            os.write(m2, (synthetic_line(500 + seq, seq * 11000) + "\n").encode())
        wait_until(lambda: [s.accepted_records for s in session.status()] == [30, 30], timeout=5)
    finally:
        session.stop()
        for fd in (m1, s1, m2, s2):
            os.close(fd)

    rx1 = read_ndjson(tmp_path / "S1" / "receivers" / "rx1.ndjson")
    rx2 = read_ndjson(tmp_path / "S1" / "receivers" / "rx2.ndjson")
    assert [r["id"] for r in rx1] == list(range(1, 31))
    assert [r["id"] for r in rx2] == list(range(501, 531))
    assert all(r["len"] == 256 and len(r["data"]) == 256 for r in rx1 + rx2)
    assert read_ndjson(tmp_path / "S1" / "events.ndjson") == []
