"""Serial port discovery and opening.

The reader never talks to pyserial directly: it gets a *resolver* (USB serial number -> the port
names that currently report it) and an *opener* (port name -> transport). Tests replace both with
fakes; real use takes the defaults below.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Protocol

BAUD_RATE = 921600  # contract Section 1: UART console, 8N1


class Transport(Protocol):
    def read(self, max_bytes: int) -> bytes:
        """Return available bytes, or b"" after a short timeout. Raise OSError when the port is gone."""

    def close(self) -> None: ...


# Every port is listed, so a serial number reported by more than one port can be detected.
Resolver = Callable[[], Dict[str, List[str]]]
Opener = Callable[[str], Transport]


def list_usb_serial_ports() -> Dict[str, List[str]]:
    """Map the USB serial number of every connected serial port to the port names reporting it."""
    from serial.tools import list_ports

    ports: Dict[str, List[str]] = {}
    for info in list_ports.comports():
        if info.serial_number:
            ports.setdefault(info.serial_number, []).append(info.device)
    return ports


def describe_ports() -> list:
    """All serial ports with their USB details, for the --list-ports command."""
    from serial.tools import list_ports

    return [
        {"port": p.device, "usb_serial_number": p.serial_number, "description": p.description,
         "vid": f"{p.vid:04X}" if p.vid is not None else None,
         "pid": f"{p.pid:04X}" if p.pid is not None else None}
        for p in sorted(list_ports.comports(), key=lambda p: p.device)
    ]


class SerialTransport:
    """A pyserial port, 8N1, opened with DTR and RTS deasserted.

    Whether opening the port resets the receiver depends on the operating system. In the GES-69
    board checks it did not on Windows, but on macOS every open reset the receiver, so a session
    there starts with the boot banner, the header row and a known gain warm-up.

    ``baud_rate`` is the contract's 921600 for receivers. Tests that use pseudo-terminals pass a
    standard rate, because a macOS pseudo-terminal rejects 921600.
    """

    def __init__(self, port: str, read_timeout_s: float = 0.2, baud_rate: int = BAUD_RATE):
        import serial

        self._serial_exception = serial.SerialException
        self._ser = serial.Serial()
        self._ser.port = port
        self._ser.baudrate = baud_rate
        self._ser.bytesize = serial.EIGHTBITS
        self._ser.parity = serial.PARITY_NONE
        self._ser.stopbits = serial.STOPBITS_ONE
        self._ser.timeout = read_timeout_s
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()

    def read(self, max_bytes: int) -> bytes:
        try:
            waiting = self._ser.in_waiting
            return self._ser.read(min(max(waiting, 1), max_bytes))
        except self._serial_exception as exc:  # unplugged port
            raise OSError(str(exc)) from exc

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


def open_serial(port: str) -> Transport:
    return SerialTransport(port)
