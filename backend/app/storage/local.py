from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO

from .base import StoredObject


class LocalStorageProvider:
    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key.lstrip("/")).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes the configured root")
        return path

    def save(self, key: str, stream: BinaryIO, content_type: str | None = None) -> StoredObject:
        del content_type
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            shutil.copyfileobj(stream, handle)
        return StoredObject(provider=self.name, key=key, local_path=target)

    def materialize(self, stored: StoredObject) -> Path:
        return stored.local_path or self._path(stored.key)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()
