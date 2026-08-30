"""Protect worker threads from truly stalled uploads without failing slow cellular.

Roadside field capture is mostly mobile-data. A 20–40 MB minute-clip on a weak
LTE link can take several minutes while bytes still trickle in — that must
succeed. We only fail when the socket goes idle (no bytes) for too long, or
hits a very long absolute ceiling.

Wi‑Fi vs cellular failure mode historically: gunicorn gthread's small fixed
pool pins forever on half-open carrier sockets; prefer waitress/Werkzeug.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

# Cap concurrent body reads — leave headroom so login/health never starve
# when several phones are on slow LTE (absolute timeouts can last minutes).
_CHUNK_SLOTS = threading.BoundedSemaphore(
    max(1, int(os.getenv("CHUNK_UPLOAD_CONCURRENCY", "3")))
)


class UploadBodyTimeout(TimeoutError):
    """Raised when the client stops sending (or overall chunk time is exceeded)."""


class _DeadlineStream:
    """Wrap wsgi.input so half-open cellular sockets don't pin a worker forever."""

    def __init__(self, stream: Any, *, idle_s: float, absolute_s: float):
        self._stream = stream
        self._idle_s = float(idle_s)
        self._deadline = time.monotonic() + float(absolute_s)
        self._sr_wrapped = True
        sock = _peer_socket_from_stream(stream)
        if sock is not None:
            try:
                sock.settimeout(self._idle_s)
            except Exception:
                pass

    def _check(self) -> None:
        if time.monotonic() > self._deadline:
            raise UploadBodyTimeout("Chunk upload exceeded absolute time limit")

    def read(self, size: int = -1) -> bytes:
        self._check()
        try:
            return self._stream.read(size)
        except OSError as e:
            raise UploadBodyTimeout(f"Chunk upload stalled: {e}") from e

    def readline(self, size: int = -1) -> bytes:
        self._check()
        try:
            return self._stream.readline(size)
        except OSError as e:
            raise UploadBodyTimeout(f"Chunk upload stalled: {e}") from e

    def readlines(self, hint: int = -1):
        self._check()
        return self._stream.readlines(hint)

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _peer_socket_from_stream(stream: Any) -> Any:
    for attr in ("_sock", "socket", "sock", "_stream"):
        obj = getattr(stream, attr, None)
        if obj is None:
            continue
        if hasattr(obj, "settimeout") and hasattr(obj, "recv"):
            return obj
        nested = _peer_socket_from_stream(obj)
        if nested is not None:
            return nested
    return None


def peer_socket_from_environ(environ: dict) -> Any:
    for key in ("gunicorn.socket", "gunicorn.sock", "werkzeug.socket"):
        sock = environ.get(key)
        if sock is not None and hasattr(sock, "settimeout"):
            return sock
    return _peer_socket_from_stream(environ.get("wsgi.input"))


def arm_chunk_body_timeouts(environ: dict) -> None:
    """Call from before_request on chunk upload paths (before request.files is read)."""
    # Idle: no bytes for this long → fail (half-open NAT). Absolute: hard ceiling.
    idle = float(os.getenv("CHUNK_UPLOAD_IDLE_TIMEOUT_S", "90"))
    absolute = float(os.getenv("CHUNK_UPLOAD_ABSOLUTE_TIMEOUT_S", "600"))
    sock = peer_socket_from_environ(environ)
    if sock is not None:
        try:
            sock.settimeout(idle)
        except Exception:
            pass
    raw = environ.get("wsgi.input")
    if raw is None or getattr(raw, "_sr_wrapped", False):
        return
    environ["wsgi.input"] = _DeadlineStream(raw, idle_s=idle, absolute_s=absolute)


def try_acquire_chunk_slot(*, wait_s: float = 0.05) -> bool:
    return _CHUNK_SLOTS.acquire(blocking=True, timeout=wait_s)


def release_chunk_slot() -> None:
    try:
        _CHUNK_SLOTS.release()
    except ValueError:
        pass
