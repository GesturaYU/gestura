"""Serial port discovery and opening.

The reader never talks to pyserial directly: it gets a *resolver* (USB serial number -> current
port name) and an *opener* (port name -> transport). Tests replace both with fakes; real use
takes the defaults below.
"""
from __future__ import annotations

from typing import Callable, Dict, Protocol

BAUD_RATE = 921600  # contract Section 1: UART console, 8N1


class Transport(Protocol):
    def read(self, max_bytes: int) -> bytes:
        """Return available bytes, or b"" after a short timeout. Raise OSError when the port is gone."""

    def close(self) -> None: ...


Resolver = Callable[[], Dict[str, str]]
Opener = Callable[[str], Transport]


def list_usb_serial_ports() -> Dict[str, str]:
    """Map the USB serial number of every connected serial port to its port name."""
    from serial.tools import list_ports

    ports: Dict[str, str] = {}
    for info in list_ports.comports():
        if info.serial_number:
            ports.setdefault(info.serial_number, info.device)
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
    """A pyserial port opened without toggling DTR/RTS, so opening it does not reset the board."""

    def __init__(self, port: str, read_timeout_s: float = 0.2):
        import serial

        self._serial_exception = serial.SerialException
        self._ser = serial.Serial()
        self._ser.port = port
        self._ser.baudrate = BAUD_RATE
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
