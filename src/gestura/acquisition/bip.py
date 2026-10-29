"""Beep helper that works on Windows and macOS.

Windows uses winsound.Beep. macOS has no tone generator, so a short sine WAV is written
once per (frequency, duration) and played with afplay. Both calls block until the tone ends,
so the recording scripts keep the same timing on both systems.
"""
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

_cache = {}


def _wav_path(freq, ms):
    key = (freq, ms)
    if key not in _cache:
        rate = 22050
        n = int(rate * ms / 1000)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)
        )
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)
        _cache[key] = path
    return _cache[key]


def beep(freq, ms):
    if sys.platform == "win32":
        import winsound

        winsound.Beep(freq, ms)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", _wav_path(freq, ms)], check=False)
    else:
        print("\a", end="", flush=True)
