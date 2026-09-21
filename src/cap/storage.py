"""Storage abstraction.

The pipeline only talks to `Storage`, so where inputs come from and where outputs land (local
disk today, S3 / Azure Blob / Dropbox in production) is a configuration choice, not a code change.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse


@runtime_checkable
class Storage(Protocol):
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str = "") -> list[str]: ...
    def describe(self) -> str: ...


def _clean(key: str) -> str:
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"illegal storage key: {key!r}")
    return "/".join(parts)


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _p(self, key: str) -> Path:
        return self.root / _clean(key)

    def read_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic on POSIX and Windows: readers never see half-written files
        return str(p)

    def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    def list(self, prefix: str = "") -> list[str]:
        base = self._p(prefix) if prefix else self.root
        if not base.exists():
            return []
        return sorted(str(f.relative_to(self.root)).replace("\\", "/") for f in base.rglob("*") if f.is_file())

    def local_path(self, key: str) -> Path:
        return self._p(key)

    def describe(self) -> str:
        return str(self.root)


class S3Storage:
    """S3-compatible storage (AWS S3, MinIO, R2). Requires the `s3` extra."""

    def __init__(self, bucket: str, prefix: str = "", client=None):
        if client is None:
            try:
                import boto3
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("S3 storage needs boto3: pip install '.[s3]'") from e
            client = boto3.client("s3")
        self.bucket, self.prefix, self.s3 = bucket, _clean(prefix), client

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{_clean(key)}" if self.prefix else _clean(key)

    def read_bytes(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=ct)
        return f"s3://{self.bucket}/{self._k(key)}"

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception as e:
            code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            # Access denied, expired credentials, network: "missing" would make the pipeline generate
            # (and pay for) an asset that exists. Fail loudly instead.
            raise RuntimeError(f"could not check s3://{self.bucket}/{self._k(key)}: {code or e}") from e

    def list(self, prefix: str = "") -> list[str]:
        full = self._k(prefix) if prefix else self.prefix
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": full}
            if token:
                kw["ContinuationToken"] = token
            resp = self.s3.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                out.append(k[len(self.prefix) + 1 :] if self.prefix else k)
            if not resp.get("IsTruncated"):
                return sorted(out)
            token = resp["NextContinuationToken"]

    def describe(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"


def open_storage(uri: str | Path) -> Storage:
    """`./output` -> LocalStorage, `s3://bucket/prefix` -> S3Storage."""
    s = str(uri)
    if s.startswith("s3://"):
        u = urlparse(s)
        return S3Storage(u.netloc, u.path.lstrip("/"))
    return LocalStorage(s)
