# s3_utils.py
"""
S3 helper for SmartRoad.

Buckets (all configurable via .env):
  - S3_INPUT_BUCKET      : where source videos/images live           (default: smart-road-videos)
  - S3_PROCESSED_BUCKET  : where source files are moved AFTER success
                            and where annotated outputs are uploaded (default: smart-road-videos-processed)
  - S3_REPORTS_BUCKET    : DOCX detection reports                    (default: smartroad-reports)

Credentials are read from .env via python-dotenv:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, [AWS_SESSION_TOKEN], [AWS_REGION]
"""

import os
import mimetypes
from pathlib import Path
from typing import List, Optional, Tuple

# --- .env loading ---
# Use the directory this file lives in so credentials are found regardless of CWD.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except Exception:
    pass

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:
    boto3 = None
    BotoCoreError = Exception
    ClientError = Exception


DEFAULT_INPUT_BUCKET = "smart-road-videos"
DEFAULT_PROCESSED_BUCKET = "smart-road-videos-processed"
DEFAULT_REPORTS_BUCKET = "smartroad-reports"
DEFAULT_REGION = "ap-south-1"  # Mumbai
DEFAULT_PRESIGN_EXPIRY = 7 * 24 * 3600

VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
GPS_EXTS = (".csv", ".xlsx", ".xls", ".json")
MEDIA_EXTS = VIDEO_EXTS + IMAGE_EXTS


def _require_boto3():
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for S3 features. Install with: pip install boto3 python-dotenv"
        )


def get_input_bucket() -> str:
    return os.getenv("S3_INPUT_BUCKET", DEFAULT_INPUT_BUCKET)


def get_processed_bucket() -> str:
    return os.getenv("S3_PROCESSED_BUCKET", DEFAULT_PROCESSED_BUCKET)


def get_reports_bucket() -> str:
    return os.getenv("S3_REPORTS_BUCKET", DEFAULT_REPORTS_BUCKET)


def get_reporter_bucket() -> str:
    """Citizen complaint uploads (raw, not processed). Defaults to input bucket."""
    return os.getenv("S3_REPORTER_BUCKET") or get_input_bucket()


def get_region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or DEFAULT_REGION


def is_s3_configured() -> bool:
    if boto3 is None:
        return False
    has_keys = bool(os.getenv("AWS_ACCESS_KEY_ID")) and bool(os.getenv("AWS_SECRET_ACCESS_KEY"))
    if has_keys:
        return True
    return os.getenv("AWS_USE_INSTANCE_PROFILE", "").lower() in ("1", "true", "yes")


_s3_client = None
_s3_transfer = None


def _client():
    global _s3_client
    if _s3_client is None:
        _require_boto3()
        from botocore.config import Config as BotoConfig
        # Default botocore pool is 10 — with S3_UPLOAD_CONCURRENCY=32 most threads
        # would queue on connection checkout and look "slow" for no reason.
        pool = max(
            10,
            int(os.getenv("S3_MAX_POOL_CONNECTIONS", "0"))
            or (int(os.getenv("S3_UPLOAD_CONCURRENCY", "32")) + 10),
        )
        _s3_client = boto3.client(
            "s3",
            region_name=get_region(),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
            config=BotoConfig(
                max_pool_connections=pool,
                connect_timeout=15,
                read_timeout=int(os.getenv("S3_READ_TIMEOUT_S", "300")),
                retries={"max_attempts": 5, "mode": "adaptive"},
            ),
        )
    return _s3_client


def _transfer():
    """Multipart uploader — large field videos upload much faster than single PUT.

    Defaults favour many mid-size parts in parallel (typical AceCloud→ap-south-1 path).
    Override with S3_MULTIPART_CHUNK_MB / S3_UPLOAD_CONCURRENCY.
    """
    global _s3_transfer
    if _s3_transfer is None:
        from boto3.s3.transfer import TransferConfig
        _s3_transfer = TransferConfig(
            multipart_threshold=int(os.getenv("S3_MULTIPART_THRESHOLD_MB", "8")) * 1024 * 1024,
            multipart_chunksize=int(os.getenv("S3_MULTIPART_CHUNK_MB", "16")) * 1024 * 1024,
            max_concurrency=int(os.getenv("S3_UPLOAD_CONCURRENCY", "32")),
            use_threads=True,
        )
    return _s3_transfer


def _guess_content_type(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _s3_error(action: str, detail: str, err: Exception) -> RuntimeError:
    """Normalize boto errors; call out clock skew explicitly (common on VMs)."""
    text = str(err)
    code = ""
    try:
        code = (err.response or {}).get("Error", {}).get("Code", "")  # type: ignore[attr-defined]
    except Exception:
        pass
    if code == "RequestTimeTooSkewed" or "RequestTimeTooSkewed" in text:
        return RuntimeError(
            f"{action} failed: AWS RequestTimeTooSkewed — this server's clock is wrong. "
            f"On the host run: timedatectl status && sudo timedatectl set-ntp true "
            f"(skew vs AWS must be < ~15 minutes). Detail: {detail}: {err}"
        )
    return RuntimeError(f"{action} failed: {detail}: {err}")


# ---------------- LIST ----------------
def list_media_keys(bucket: Optional[str] = None, prefix: str = "") -> List[str]:
    """List all video/image keys in the given bucket+prefix. Returns sorted list."""
    _require_boto3()
    bucket = bucket or get_input_bucket()
    keys: List[str] = []
    paginator = _client().get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                k = obj["Key"]
                if k.lower().endswith(MEDIA_EXTS):
                    keys.append(k)
    except (BotoCoreError, ClientError) as e:
        raise _s3_error("S3 list", f"s3://{bucket}/{prefix}", e)
    return sorted(keys)


def find_sibling_gps_key(bucket: str, media_key: str) -> Optional[str]:
    """
    Given a media key like videos/VID_xxx.mp4, look for a GPS log sibling.

    Prefer CSV/XLSX (field capture GPS) over JSON (frame metadata), so detection
    does not pick the frame-meta JSON when both are present.
    """
    _require_boto3()
    if not bucket:
        bucket = get_input_bucket()
    base, _ = os.path.splitext(media_key)

    tabular = (".csv", ".xlsx", ".xls")
    candidates = [base + ext for ext in tabular]
    candidates += [base + "_log" + ext for ext in tabular]
    candidates += [base + "_log.json", base + "_frames.json", base + ".json"]

    for candidate in candidates:
        try:
            _client().head_object(Bucket=bucket, Key=candidate)
            return candidate
        except ClientError:
            continue
        except BotoCoreError:
            continue
    return None


def find_sibling_json_key(bucket: str, media_key: str) -> Optional[str]:
    """JSON sibling for Detection preview panel (frame meta / GPS JSON)."""
    _require_boto3()
    if not bucket:
        bucket = get_input_bucket()
    base, _ = os.path.splitext(media_key)
    for candidate in (base + "_log.json", base + "_frames.json", base + ".json"):
        try:
            _client().head_object(Bucket=bucket, Key=candidate)
            return candidate
        except ClientError:
            continue
        except BotoCoreError:
            continue
    return None


# ---------------- DOWNLOAD ----------------
def download_file(bucket: str, key: str, local_path: str) -> str:
    _require_boto3()
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    try:
        _client().download_file(bucket, key, local_path)
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"S3 download failed s3://{bucket}/{key} -> {local_path}: {e}")
    return local_path


# ---------------- UPLOAD ----------------
def upload_file(local_path: str, key: str, bucket: Optional[str] = None, public: bool = False) -> str:
    _require_boto3()
    if not local_path or not os.path.exists(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")
    bucket = bucket or get_processed_bucket()
    extra = {"ContentType": _guess_content_type(local_path)}
    if public:
        extra["ACL"] = "public-read"
    try:
        _client().upload_file(
            local_path,
            bucket,
            key,
            ExtraArgs=extra,
            Config=_transfer(),
        )
    except (BotoCoreError, ClientError) as e:
        raise _s3_error("S3 upload", f"{local_path} -> s3://{bucket}/{key}", e)
    return f"s3://{bucket}/{key}"


def presign_url(key: str, bucket: Optional[str] = None, expires_in: int = DEFAULT_PRESIGN_EXPIRY) -> str:
    _require_boto3()
    bucket = bucket or get_processed_bucket()
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"Failed to presign s3://{bucket}/{key}: {e}")


def presign_download_url(
    key: str,
    *,
    bucket: Optional[str] = None,
    filename: Optional[str] = None,
    expires_in: int = 3600,
) -> str:
    """Presigned GET that forces browser download (Content-Disposition: attachment)."""
    _require_boto3()
    bucket = bucket or get_processed_bucket()
    name = (filename or os.path.basename(key) or "download.bin").replace('"', "")
    params = {
        "Bucket": bucket,
        "Key": key,
        "ResponseContentDisposition": f'attachment; filename="{name}"',
    }
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"Failed to presign download s3://{bucket}/{key}: {e}")


def upload_and_link(
    local_path: str,
    key: str,
    bucket: Optional[str] = None,
    public: bool = False,
    expires_in: int = DEFAULT_PRESIGN_EXPIRY,
) -> Tuple[str, str]:
    s3_uri = upload_file(local_path, key, bucket=bucket, public=public)
    bucket = bucket or get_processed_bucket()
    if public:
        url = f"https://{bucket}.s3.{get_region()}.amazonaws.com/{key}"
    else:
        url = presign_url(key, bucket=bucket, expires_in=expires_in)
    return s3_uri, url


# ---------------- MULTIPART (presigned) — large field videos 2–10 GB ----------------
# Parts live in S3 under the same key until CompleteMultipartUpload merges them
# into ONE object. Detection then reads that single key (no ffmpeg reassembly).
DEFAULT_MULTIPART_PART_SIZE = 64 * 1024 * 1024  # 64 MiB
MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024       # AWS minimum (except last part)
MULTIPART_PRESIGN_EXPIRES = 6 * 3600            # 6h for slow field networks


def create_multipart_upload(
    key: str,
    *,
    bucket: Optional[str] = None,
    content_type: str = "video/mp4",
) -> dict:
    """Start an S3 multipart upload; returns upload_id + key + bucket."""
    _require_boto3()
    bucket = bucket or get_input_bucket()
    try:
        resp = _client().create_multipart_upload(
            Bucket=bucket,
            Key=key,
            ContentType=content_type or "application/octet-stream",
        )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"create_multipart_upload failed s3://{bucket}/{key}: {e}")
    return {
        "bucket": bucket,
        "key": key,
        "upload_id": resp["UploadId"],
        "content_type": content_type,
    }


def presign_multipart_part(
    key: str,
    upload_id: str,
    part_number: int,
    *,
    bucket: Optional[str] = None,
    expires_in: int = MULTIPART_PRESIGN_EXPIRES,
) -> str:
    """Presigned PUT URL for one part (1…10000). Client uploads bytes directly to S3."""
    _require_boto3()
    bucket = bucket or get_input_bucket()
    if part_number < 1 or part_number > 10000:
        raise ValueError("part_number must be 1..10000")
    try:
        return _client().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": int(part_number),
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"presign upload_part failed: {e}")


def upload_part_file(
    local_path: str,
    key: str,
    upload_id: str,
    part_number: int,
    *,
    bucket: Optional[str] = None,
) -> dict:
    """Upload one multipart part from disk via boto3 (Flask relay when phone cannot reach S3)."""
    _require_boto3()
    bucket = bucket or get_input_bucket()
    if part_number < 1 or part_number > 10000:
        raise ValueError("part_number must be 1..10000")
    if not local_path or not os.path.exists(local_path):
        raise FileNotFoundError(f"Part file not found: {local_path}")
    try:
        with open(local_path, "rb") as fh:
            resp = _client().upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=int(part_number),
                Body=fh,
            )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"upload_part failed part={part_number} s3://{bucket}/{key}: {e}")
    etag = str(resp.get("ETag") or "").strip().strip('"')
    if not etag:
        raise RuntimeError(f"upload_part returned no ETag for part {part_number}")
    return {"PartNumber": int(part_number), "ETag": etag}


def complete_multipart_upload(
    key: str,
    upload_id: str,
    parts: List[dict],
    *,
    bucket: Optional[str] = None,
) -> str:
    """Finish multipart — S3 concatenates parts into one object. parts: [{PartNumber, ETag}]."""
    _require_boto3()
    bucket = bucket or get_input_bucket()
    cleaned = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        num = int(p.get("PartNumber") or p.get("part_number") or 0)
        etag = str(p.get("ETag") or p.get("etag") or "").strip()
        if num < 1 or not etag:
            continue
        if not etag.startswith('"'):
            etag = f'"{etag}"'
        cleaned.append({"PartNumber": num, "ETag": etag})
    if not cleaned:
        raise ValueError("No valid parts to complete multipart upload")
    cleaned.sort(key=lambda x: x["PartNumber"])
    try:
        _client().complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": cleaned},
        )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"complete_multipart_upload failed s3://{bucket}/{key}: {e}")
    return f"s3://{bucket}/{key}"


def abort_multipart_upload(
    key: str,
    upload_id: str,
    *,
    bucket: Optional[str] = None,
) -> None:
    _require_boto3()
    bucket = bucket or get_input_bucket()
    try:
        _client().abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"abort_multipart_upload failed: {e}")


# ---------------- READ helpers (for UI preview) ----------------
def presign_input_url(key: str, expires_in: int = 3600) -> str:
    """Presigned GET URL for a key in the INPUT bucket — used to stream video in the UI."""
    return presign_url(key, bucket=get_input_bucket(), expires_in=expires_in)


def read_text_object(bucket: str, key: str, max_bytes: int = 5 * 1024 * 1024) -> str:
    """Fetch a small text object (e.g. JSON) from S3 and return as string. Capped at max_bytes."""
    _require_boto3()
    try:
        obj = _client().get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read(max_bytes)
        return body.decode("utf-8", errors="replace")
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"Failed to read s3://{bucket}/{key}: {e}")


def head_exists(bucket: str, key: str) -> bool:
    _require_boto3()
    try:
        _client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


# ---------------- MOVE (copy + delete) ----------------
def delete_object(bucket: str, key: str) -> None:
    """Permanently delete a single object from S3."""
    _require_boto3()
    try:
        _client().delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"S3 delete failed s3://{bucket}/{key}: {e}")


def delete_prefix(bucket: str, prefix: str) -> int:
    """Delete every object under ``prefix`` (batched). Returns deleted count."""
    _require_boto3()
    if not bucket or not prefix:
        return 0
    cli = _client()
    deleted = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            resp = cli.list_objects_v2(**kwargs)
        except (BotoCoreError, ClientError) as e:
            raise RuntimeError(f"S3 list failed s3://{bucket}/{prefix}: {e}")
        objs = resp.get("Contents") or []
        if not objs:
            break
        for i in range(0, len(objs), 1000):
            chunk = objs[i : i + 1000]
            try:
                cli.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": o["Key"]} for o in chunk], "Quiet": True},
                )
            except (BotoCoreError, ClientError) as e:
                raise RuntimeError(f"S3 delete_objects failed s3://{bucket}/{prefix}: {e}")
            deleted += len(chunk)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return deleted


def copy_object(
    src_bucket: str,
    src_key: str,
    dst_bucket: str,
    dst_key: Optional[str] = None,
) -> str:
    """
    Server-side copy from src to dst. Unlike move_object, the source is left
    in place — use this for files that may need to be read again later (e.g.
    a GPS log sibling that should still be found if the video is reprocessed).
    Returns the destination s3:// URI on success.
    """
    _require_boto3()
    dst_key = dst_key or src_key
    cli = _client()
    try:
        cli.copy_object(
            Bucket=dst_bucket,
            Key=dst_key,
            CopySource={"Bucket": src_bucket, "Key": src_key},
            MetadataDirective="COPY",
        )
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(
            f"S3 copy failed s3://{src_bucket}/{src_key} -> s3://{dst_bucket}/{dst_key}: {e}"
        )
    return f"s3://{dst_bucket}/{dst_key}"


def move_object(
    src_bucket: str,
    src_key: str,
    dst_bucket: str,
    dst_key: Optional[str] = None,
) -> str:
    """
    Server-side copy from src to dst, then delete the source.
    Returns the destination s3:// URI on success.
    """
    _require_boto3()
    dst_key = dst_key or src_key
    cli = _client()
    try:
        cli.copy_object(
            Bucket=dst_bucket,
            Key=dst_key,
            CopySource={"Bucket": src_bucket, "Key": src_key},
            MetadataDirective="COPY",
        )
        cli.delete_object(Bucket=src_bucket, Key=src_key)
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(
            f"S3 move failed s3://{src_bucket}/{src_key} -> s3://{dst_bucket}/{dst_key}: {e}"
        )
    return f"s3://{dst_bucket}/{dst_key}"


def object_exists(bucket: str, key: str) -> bool:
    """True if head_object succeeds for bucket/key."""
    _require_boto3()
    try:
        _client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False
    except BotoCoreError:
        return False


def rename_object_safe(
    bucket: str,
    src_key: str,
    dst_key: str,
    *,
    delete_source: bool = True,
) -> str:
    """
    Copy src→dst in the same bucket, verify the destination exists, then
    optionally delete the source. Never deletes source if destination is missing.
    Returns s3:// URI of the new key.
    """
    _require_boto3()
    if not src_key or not dst_key:
        raise ValueError("src_key and dst_key are required")
    if src_key == dst_key:
        return f"s3://{bucket}/{dst_key}"
    if not object_exists(bucket, src_key):
        raise FileNotFoundError(f"S3 source missing: s3://{bucket}/{src_key}")
    copy_object(bucket, src_key, bucket, dst_key)
    if not object_exists(bucket, dst_key):
        raise RuntimeError(
            f"S3 rename aborted — destination not found after copy: s3://{bucket}/{dst_key}"
        )
    if delete_source:
        try:
            _client().delete_object(Bucket=bucket, Key=src_key)
        except (BotoCoreError, ClientError) as e:
            print(f"[S3] rename: destination ok but source delete failed: {e}")
    return f"s3://{bucket}/{dst_key}"


def upload_report(local_path: str, key: str, *, public: bool = False) -> str:
    """Upload a DOCX (or other) report into the reports bucket."""
    return upload_file(local_path, key, bucket=get_reports_bucket(), public=public)
