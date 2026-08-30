#!/usr/bin/env python3
"""Measure AceCloud host → AWS S3 latency (run ON the server).

Usage (on AceCloud, from repo root):
  python scripts/probe_s3_latency.py

Prints configured vs actual bucket region, TCP/TLS RTT to regional endpoints,
and a small HeadBucket / Put/Get of a tiny probe object (deleted afterward).
Never prints secret keys.
"""
from __future__ import annotations

import os
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass


def _tcp_tls_ms(host: str, port: int = 443, timeout: float = 5.0) -> tuple[float, float]:
    """Return (tcp_connect_ms, tls_handshake_ms)."""
    t0 = time.perf_counter()
    raw = socket.create_connection((host, port), timeout=timeout)
    t_tcp = (time.perf_counter() - t0) * 1000
    ctx = ssl.create_default_context()
    t1 = time.perf_counter()
    wrapped = ctx.wrap_socket(raw, server_hostname=host)
    t_tls = (time.perf_counter() - t1) * 1000
    wrapped.close()
    return t_tcp, t_tls


def main() -> int:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-south-1"
    bucket = os.getenv("S3_INPUT_BUCKET", "smart-road-videos")
    print(f"Host probe on Python {sys.version.split()[0]}")
    print(f"configured AWS_REGION = {region}")
    print(f"S3_INPUT_BUCKET       = {bucket}")
    print()

    # Bucket's true home region
    s3 = boto3.client(
        "s3",
        region_name=region,
        config=Config(connect_timeout=8, read_timeout=20, retries={"max_attempts": 2}),
    )
    try:
        loc = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint")
        true_region = loc or "us-east-1"
    except Exception as e:
        true_region = f"(error: {type(e).__name__}: {e})"
    print(f"bucket LocationConstraint = {true_region}")
    print()

    regions = ["ap-south-1", "ap-south-2", "ap-southeast-1", "us-east-1"]
    print(f"{'region':16} {'tcp_ms':>8} {'tls_ms':>8} {'head_ms':>8}  note")
    best = None
    for r in regions:
        host = f"s3.{r}.amazonaws.com"
        try:
            tcp_ms, tls_ms = _tcp_tls_ms(host)
        except Exception as e:
            print(f"{r:16} {'—':>8} {'—':>8} {'—':>8}  connect fail: {e}")
            continue
        c = boto3.client(
            "s3",
            region_name=r,
            config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}),
        )
        t0 = time.perf_counter()
        note = "ok"
        try:
            c.head_bucket(Bucket=bucket)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            note = f"ClientError {code}"
        except Exception as e:
            note = type(e).__name__
        head_ms = (time.perf_counter() - t0) * 1000
        print(f"{r:16} {tcp_ms:8.0f} {tls_ms:8.0f} {head_ms:8.0f}  {note}")
        if best is None or head_ms < best[0]:
            best = (head_ms, r)

    print()
    if best:
        print(f"Fastest HeadBucket from THIS host: {best[1]} (~{best[0]:.0f} ms)")
        if str(true_region) not in ("(error",) and best[1] != str(true_region):
            print(
                f"NOTE: bucket lives in {true_region} but {best[1]} answered faster "
                f"from this host — cross-region Head may be redirected; "
                f"uploads should still target the bucket's home region."
            )

    # Tiny object round-trip in configured region
    print()
    key = f"_latency_probe/{int(time.time())}.txt"
    body = b"smartroad-latency-probe"
    try:
        t0 = time.perf_counter()
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")
        put_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        get_ms = (time.perf_counter() - t0) * 1000
        s3.delete_object(Bucket=bucket, Key=key)
        print(f"Put+Get tiny object in {region}: put={put_ms:.0f} ms  get={get_ms:.0f} ms  ok={data == body}")
        total = put_ms + get_ms
        if total > 400:
            print(
                "HIGH latency for tiny objects — finalize multipart will feel slow. "
                "Prefer AceCloud Mumbai region VM next to ap-south-1, or keep "
                "S3_UPLOAD_CONCURRENCY high and multipart chunks large."
            )
        elif total > 150:
            print("Moderate latency — normal for Noida (AceCloud) ↔ Mumbai (S3 ap-south-1).")
        else:
            print("Low latency — path looks healthy.")
    except Exception as e:
        print(f"Put/Get probe failed: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
