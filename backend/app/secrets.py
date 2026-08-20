from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class SecretStore:
    """Encrypts persisted credentials; environment references remain outside the database."""

    def __init__(self, encryption_key: str | None):
        self._fernet: Fernet | None = None
        if encryption_key:
            try:
                self._fernet = Fernet(encryption_key.encode())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "KNOWLEDGEDEBT_ENCRYPTION_KEY must be a valid Fernet key; "
                    "generate one with `python -c \"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\"`"
                ) from exc

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if not self._fernet:
            raise ValueError(
                "Refusing to store a plaintext credential. Configure KNOWLEDGEDEBT_ENCRYPTION_KEY "
                "or use an env:VARIABLE credential reference."
            )
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        if not self._fernet:
            raise ValueError("KNOWLEDGEDEBT_ENCRYPTION_KEY is required to decrypt this credential")
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored credential cannot be decrypted with the configured encryption key") from exc

    def resolve(self, ciphertext: str | None, reference: str | None) -> str | None:
        if ciphertext:
            return self.decrypt(ciphertext)
        if not reference:
            return None
        prefix, separator, variable = reference.partition(":")
        if prefix != "env" or not separator or not variable:
            raise ValueError("Only env:VARIABLE credential references are supported")
        return os.getenv(variable)

