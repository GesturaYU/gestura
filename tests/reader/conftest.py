"""Fake serial ports for reader tests: no boards, no OS serial devices."""
import json
import queue
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DISCONNECT = object()


def record_line(seq, *, channel=11, bandwidth=0, data=(1, -2, 3, -4)):
    payload = ",".join(str(v) for v in data)
    return (f"CSI_DATA,{seq},1a:00:00:00:00:00,-48,11,1,0,{bandwidth},1,1,0,0,0,0,-96,0,"
            f"{channel},0,{1000 * seq},0,47,0,{len(data)},0,\"[{payload}]\"\n")


HEADER = ("type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,"
          "stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,"
          "ant,sig_len,rx_state,len,first_word,data\n")


class FakePort:
    """A transport fed by the test: bytes arrive in the order pushed; DISCONNECT unplugs it."""

    def __init__(self, device):
        self.device = device
        self._queue = queue.Queue()
        self.closed = False

    def push(self, item):
        self._queue.put(item.encode() if isinstance(item, str) else item)

    def read(self, max_bytes):
        if self.closed:
            raise OSError("port closed")
        try:
            item = self._queue.get(timeout=0.01)
        except queue.Empty:
            return b""
        if item is DISCONNECT:
            self.closed = True
            raise OSError(f"{self.device} unplugged")
        return item

    def close(self):
        self.closed = True


class FakeBus:
    """What the operating system would report: USB serial number -> port, and the ports."""

    def __init__(self):
        self.serials = {}  # USB serial number -> port names reporting it
        self.ports = {}

    def plug(self, serial, device):
        port = FakePort(device)
        self.serials.setdefault(serial, []).append(device)
        self.ports[device] = port
        return port

    def unplug(self, serial):
        for device in self.serials.pop(serial):
            self.ports[device].push(DISCONNECT)

    def resolver(self):
        return {serial: list(devices) for serial, devices in self.serials.items()}

    def opener(self, device):
        port = self.ports.get(device)
        if port is None or port.closed:
            raise OSError(f"cannot open {device}")
        return port


@pytest.fixture
def bus():
    return FakeBus()


def read_ndjson(path):
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")
