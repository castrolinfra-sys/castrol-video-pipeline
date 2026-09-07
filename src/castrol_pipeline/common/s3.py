"""Object storage: key builders, sha256-on-write, put/get, presign, CDN URLs.

Two backends behind one interface. `s3` is production. `local` writes under a
directory and exists so the orchestrator can be driven end-to-end without AWS
credentials — the same reason the stub stages exist.

The bucket is private and stays private. Reads happen two ways, and the
difference is not cosmetic:

  * **Presigned GET** — for handing a working artefact to a vendor. apimart and
    kie fetch inputs BY URL, so the mp3 and the edited image must be reachable
    for the length of a render. Short-lived, unlisted, method-bound.
  * **CDN URL** — for the delivered video only. SigV4 caps presign expiry at 7
    days and the client link must live 6 months, so a delivered link is a plain
    CloudFront URL over an unguessable key. It stops working because the
    lifecycle rule DELETES the object, not because a signature lapsed.

ONE KEY, VERBATIM, EVERYWHERE
Keys include the `S3_PREFIX` from the moment they are built. No layer prepends
anything. This is deliberate: the CloudFront distribution has no Origin Path,
so the full key including `castrol/` must appear in the URL, and a backend that
silently prefixed on write while `cdn_url()` prefixed again produced a 403 that
reads exactly like a permissions failure. What goes in `assets.s3_key` is now
the same string you can paste into `aws s3 cp`.
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
    """A written object. `key` is the FULL key, prefix included."""

    key: str
    sha256: str
    bytes: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------- key builders --
# Every builder returns a full key. `S3_PREFIX` is applied here and nowhere
# else, so there is exactly one place it can be wrong.


def _prefix() -> str:
    p = get_settings().s3_prefix
    return p if p.endswith("/") or p == "" else p + "/"


def plate_key(uniform_id: str, background_id: str, sha256: str) -> str:
    return f"{_prefix()}plates/{uniform_id}_{background_id}/{sha256}.png"


def job_key(job_id: str, filename: str) -> str:
    """Working artefacts. Never expired by lifecycle — they are the evidence
    you need to explain a video that shipped five months ago."""
    return f"{_prefix()}jobs/{job_id}/{filename}"


def delivery_key() -> str:
    """A fresh UUID prefix, deliberately not job_id.

    The public URL then leaks no internal identifier and is not enumerable, and
    the 180-day lifecycle rule can target `deliver/` alone without destroying
    the working artefacts. This uuid is the whole security model for a
    delivered link, so it must never be derived from a phone number, a job_id,
    or anything else guessable.
    """
    return f"{_prefix()}deliver/{uuid.uuid4()}/video.mp4"


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
    def copy(self, src_key: str, dst_key: str) -> StoredObject: ...
    def presigned_get_url(self, key: str, *, expires_in: int = 21600) -> str: ...
    def download(self, key: str, dest: Path) -> Path: ...


class LocalBackend:
    """Filesystem-backed store for local runs and tests."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

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
        digest = _sha256_file(path)
        shutil.copyfile(path, dest)
        obj = StoredObject(key=key, sha256=digest, bytes=dest.stat().st_size)
        log.info("storage.put_file", backend="local", key=key, bytes=obj.bytes)
        return obj

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def copy(self, src_key: str, dst_key: str) -> StoredObject:
        src, dst = self._path(src_key), self._path(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return StoredObject(key=dst_key, sha256=_sha256_file(dst), bytes=dst.stat().st_size)

    def download(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), dest)
        return dest

    def presigned_get_url(self, key: str, *, expires_in: int = 21600) -> str:
        raise NotImplementedError(
            "The local backend cannot produce a URL a vendor can fetch. "
            "apimart and kie pull inputs over HTTP, so any stage that reaches a "
            "real vendor needs STORAGE_BACKEND=s3."
        )


class S3Backend:
    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.region = region
        # The endpoint and the signature region MUST match the bucket's region.
        # boto3's default resolves the global host `<bucket>.s3.amazonaws.com`,
        # and a SigV4 signature made against that does not validate for a
        # bucket in another region: the presigned URL 403s while the SDK's own
        # calls succeed, which reads as an IAM problem and is not one. Pinning
        # both is what fixed it, verified against a real ap-south-1 bucket.
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
                retries={"max_attempts": 5, "mode": "standard"},
            ),
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> StoredObject:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        obj = StoredObject(key=key, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        log.info("storage.put", backend="s3", key=key, bytes=obj.bytes)
        return obj

    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> StoredObject:
        digest = _sha256_file(path)
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra or None)
        obj = StoredObject(key=key, sha256=digest, bytes=path.stat().st_size)
        log.info("storage.put_file", backend="s3", key=key, bytes=obj.bytes)
        return obj

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def copy(self, src_key: str, dst_key: str) -> StoredObject:
        """Server-side copy. No egress, no round trip through this machine.

        Publishing copies rather than moves because the two copies have
        different lifetimes on purpose: the delivered object expires at 180
        days, the working artefact under `jobs/` never does. Expiring the
        client's link must not destroy the evidence of how the video was made.
        """
        self.client.copy_object(
            Bucket=self.bucket,
            Key=dst_key,
            CopySource={"Bucket": self.bucket, "Key": src_key},
            MetadataDirective="COPY",
        )
        head = self.client.head_object(Bucket=self.bucket, Key=dst_key)
        # ETag is not a sha256 for multipart objects, so re-derive nothing here;
        # the caller already knows the digest of what it published.
        obj = StoredObject(key=dst_key, sha256="", bytes=int(head["ContentLength"]))
        log.info("storage.copy", backend="s3", src=src_key, dst=dst_key, bytes=obj.bytes)
        return obj

    def download(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(dest))
        return dest

    def presigned_get_url(self, key: str, *, expires_in: int = 21600) -> str:
        """A URL a vendor can GET. Default 6h.

        Six hours, not one: the video step can queue for 20 minutes and has
        been seen to take 20 more, and a link that dies mid-render fails the
        stage for a reason no log explains.

        The signature is bound to the METHOD as well as the key — a HEAD
        against a URL signed for GET returns 403. That is correct behaviour,
        not a broken URL; verify these with GET.
        """
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        s = get_settings()
        if s.storage_backend == "s3":
            _backend = S3Backend(
                s.require("s3_bucket"),
                s.aws_region,
                s.aws_access_key_id,
                s.aws_secret_access_key,
            )
        else:
            _backend = LocalBackend(Path(s.local_storage_dir))
    return _backend


def reset_storage() -> None:
    global _backend
    _backend = None


def cdn_url(key: str) -> str:
    """The public URL for a full key.

    The distribution has NO Origin Path, so the key goes in verbatim — prefix
    included. `.../castrol/deliver/x/video.mp4` is 200 and `.../deliver/x/video.mp4`
    is 403.
    """
    base = get_settings().require("cdn_base_url").rstrip("/")
    return f"{base}/{key.lstrip('/')}"
