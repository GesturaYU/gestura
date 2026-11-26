"""The real pyserial path: pseudo-terminals instead of boards (Linux and macOS), and port settings."""
import os
import sys

import pytest

from conftest import read_ndjson, wait_until
from gestura.acquisition.reader.fake_receiver import HEADER, synthetic_line
from gestura.acquisition.reader.ports import SerialTransport
from gestura.acquisition.reader.reader import CaptureSession, ReceiverSpec

needs_pty = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")

# A macOS pseudo-terminal rejects the contract's 921600 baud ("Inappropriate ioctl for device"),
# so the test opens the pseudo-terminals at a standard rate. Real receivers use 921600.
PTY_BAUD_RATE = 115200


def open_pty_port(port):
    return SerialTransport(port, baud_rate=PTY_BAUD_RATE)


def make_pty():
    import tty

    master, slave = os.openpty()
    tty.setraw(slave)
    return master, os.ttyname(slave), slave


@needs_pty
def test_two_receivers_over_real_serial_ports(tmp_path):
    m1, dev1, s1 = make_pty()
    m2, dev2, s2 = make_pty()
    serials = {"SER-B2": [dev1], "SER-B3": [dev2]}
    session = CaptureSession(tmp_path / "S1",
                             [ReceiverSpec("rx1", "B2", "SER-B2"),
                              ReceiverSpec("rx2", "B3", "SER-B3")],
                             resolver=lambda: dict(serials), opener=open_pty_port)
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


def test_receivers_are_opened_at_the_contract_baud_rate(monkeypatch):
    import serial

    opened = {}

    def fake_open(self):
        opened["baudrate"] = self.baudrate
        opened["dtr"], opened["rts"] = self.dtr, self.rts

    monkeypatch.setattr(serial.Serial, "open", fake_open)
    from gestura.acquisition.reader.ports import open_serial
    open_serial("/dev/fake")
    assert opened == {"baudrate": 921600, "dtr": False, "rts": False}
