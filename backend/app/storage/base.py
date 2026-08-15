from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    provider: str
    key: str
    local_path: Path | None = None


class StorageProvider(Protocol):
    name: str

    def save(self, key: str, stream: BinaryIO, content_type: str | None = None) -> StoredObject: ...

    def materialize(self, stored: StoredObject) -> Path: ...

    def delete(self, key: str) -> None: ...
