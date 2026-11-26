"""Beep helper that works on Windows and macOS.

Windows uses winsound.Beep. On macOS one sounddevice output stream is opened on the first
tone and kept open until the program exits; each tone is a sine written to that stream, so it
starts about 30 ms (the stream's fixed output latency) after the call. If sounddevice is not
installed, macOS falls back to a short sine WAV written once per (frequency, duration) and
played with afplay, which can start 0.3-0.9 s late; a warning is printed once. Both paths
block for about the length of the tone, so the recording scripts keep the same timing on
both systems.
"""
import atexit
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
        _beep_stream(freq, ms)
    else:
        print("\a", end="", flush=True)


_stream = None
_warned_afplay = False


def _close_stream():
    global _stream
    if _stream is not None:
        _stream.stop()
        _stream.close()
        _stream = None


def _beep_stream(freq, ms, rate=44100):
    """macOS: one audio stream stays open, so a tone starts about 30 ms after the call
    (afplay needed 0.3-0.9 s to start)."""
    global _stream, _warned_afplay
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:  # fallback: old behaviour
        if not _warned_afplay:
            print(
                "sounddevice is not installed: cue tones use afplay and can start 0.3-0.9 s late. "
                "Run pip install -r requirements.txt."
            )
            _warned_afplay = True
        subprocess.run(["afplay", _wav_path(freq, ms)], check=False)
        return
    if _stream is None:
        _stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", latency="low")
        _stream.start()
        atexit.register(_close_stream)
    t = np.arange(int(rate * ms / 1000)) / rate
    _stream.write((0.3 * np.sin(2 * np.pi * freq * t)).astype("float32").reshape(-1, 1))
