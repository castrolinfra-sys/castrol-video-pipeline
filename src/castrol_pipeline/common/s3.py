"""Object storage: key builders, sha256-on-write, put/get.

Two backends behind one interface. `s3` is production. `local` writes under a
directory and exists so the orchestrator can be driven end-to-end without AWS
credentials — the same reason the stub stages exist.

The bucket is private and stays private. Delivery links are unguessable key +
private bucket + CDN + lifecycle rule, never presigned URLs: SigV4 caps expiry
at 7 days and a URL signed with instance-role credentials dies with the session
token, typically within the hour.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import get_settings
from .logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    bytes: int


# ------------------------------------------------------------- key builders --


def plate_key(uniform_id: str, background_id: str, sha256: str) -> str:
    return f"plates/{uniform_id}_{background_id}/{sha256}.png"


def job_key(job_id: str, filename: str) -> str:
    return f"jobs/{job_id}/{filename}"


def delivery_key() -> str:
    """A fresh UUID prefix, deliberately not job_id.

    The public URL then leaks no internal identifier and is not enumerable, and
    the 180-day lifecycle rule can target `deliver/` alone without destroying
    the working artefacts needed to diagnose a delivered video.
    """
    return f"deliver/{uuid.uuid4()}/video.mp4"


# ----------------------------------------------------------------- backends --


class StorageBackend(Protocol):
    def put(
        self, key: str, data: bytes, *, content_type: str | None = None
    ) -> StoredObject: ...
    def put_file(
        self, key: str, path: Path, *, content_type: str | None = None
    ) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalBackend:
    """Filesystem-backed store for local runs and tests."""

    def __init__(self, root: Path, prefix: str = "") -> None:
        self.root = root
        self.prefix = prefix

    def _path(self, key: str) -> Path:
        return self.root / (self.prefix + key)

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        obj = StoredObject(key=key, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        log.info("storage.put", backend="local", key=key, bytes=obj.bytes)
        return obj

    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> StoredObject:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        shutil.copyfile(path, dest)
        obj = StoredObject(key=key, sha256=digest.hexdigest(), bytes=dest.stat().st_size)
        log.info("storage.put_file", backend="local", key=key, bytes=obj.bytes)
        return obj

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Backend:
    def __init__(self, bucket: str, prefix: str, region: str) -> None:
        import boto3

        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client("s3", region_name=region)

    def _full(self, key: str) -> str:
        return self.prefix + key

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StoredObject:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=self._full(key), Body=data, **extra)
        obj = StoredObject(key=key, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        log.info("storage.put", backend="s3", key=key, bytes=obj.bytes)
        return obj

    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> StoredObject:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(
            str(path), self.bucket, self._full(key), ExtraArgs=extra or None
        )
        obj = StoredObject(key=key, sha256=digest.hexdigest(), bytes=path.stat().st_size)
        log.info("storage.put_file", backend="s3", key=key, bytes=obj.bytes)
        return obj

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._full(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._full(key))
            return True
        except ClientError:
            return False


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        s = get_settings()
        if s.storage_backend == "s3":
            _backend = S3Backend(s.require("s3_bucket"), s.s3_prefix, s.aws_region)
        else:
            _backend = LocalBackend(Path(s.local_storage_dir), s.s3_prefix)
    return _backend


def reset_storage() -> None:
    global _backend
    _backend = None


def cdn_url(key: str) -> str:
    base = get_settings().require("cdn_base_url").rstrip("/")
    return f"{base}/{get_settings().s3_prefix}{key}"
