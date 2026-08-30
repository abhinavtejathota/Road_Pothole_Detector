"""Safe body streaming for binary chunk uploads.

Werkzeug + Flask MAX_CONTENT_LENGTH: if Content-Length is missing,
``request.stream`` becomes an empty BytesIO (safe_fallback). Expo
FileSystem.uploadAsync often omits Content-Length → empty chunk → 400.

Always open the raw WSGI body with safe_fallback=False for chunk-bin.
"""
from __future__ import annotations

from typing import Any, BinaryIO


def open_upload_body_stream(environ: dict, *, max_content_length: int | None) -> BinaryIO:
    """Return a readable stream of the request body that works without Content-Length."""
    from werkzeug.wsgi import get_input_stream

    return get_input_stream(
        environ,
        max_content_length=max_content_length,
        safe_fallback=False,
    )


def drain_stream_to_file(stream: Any, dest_path: str, *, buf_size: int = 1024 * 1024) -> int:
    """Write stream to dest_path; return bytes written. Propagates UploadBodyTimeout."""
    written = 0
    with open(dest_path, "wb") as out:
        while True:
            block = stream.read(buf_size)
            if not block:
                break
            out.write(block)
            written += len(block)
    return written
