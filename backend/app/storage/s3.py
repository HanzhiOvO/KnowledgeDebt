from __future__ import annotations

import tempfile
from pathlib import Path
from typing import BinaryIO

from .base import StoredObject


class S3StorageProvider:
    """Optional S3-compatible storage implementation.

    boto3 is imported only when this provider is selected, keeping the default
    local deployment small.
    """

    name = "s3"

    def __init__(self, bucket: str, endpoint_url: str | None = None):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional deployment path
            raise RuntimeError("Install the 's3' backend extra to use S3 storage") from exc
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url)

    def save(self, key: str, stream: BinaryIO, content_type: str | None = None) -> StoredObject:
        if content_type:
            self.client.upload_fileobj(stream, self.bucket, key, ExtraArgs={"ContentType": content_type})
        else:
            self.client.upload_fileobj(stream, self.bucket, key)
        return StoredObject(provider=self.name, key=key)

    def materialize(self, stored: StoredObject) -> Path:
        suffix = Path(stored.key).suffix
        handle = tempfile.NamedTemporaryFile(prefix="knowledgedebt-", suffix=suffix, delete=False)
        handle.close()
        target = Path(handle.name)
        self.client.download_file(self.bucket, stored.key, str(target))
        return target

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
