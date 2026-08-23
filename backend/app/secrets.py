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
                    "KNOWLEDGEDEBT_ENCRYPTION_KEY 不是有效的 Fernet 密钥；"
                    "请按文档重新生成，或在原生应用中恢复对应钥匙串项目。"
                ) from exc

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if not self._fernet:
            raise ValueError(
                "已拒绝明文保存 Provider 凭据。请配置加密主密钥，或使用 env:VARIABLE 引用。"
            )
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        if not self._fernet:
            raise ValueError("缺少加密主密钥，无法读取已保存的 Provider 凭据。")
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("当前加密主密钥无法解密已保存的 Provider 凭据，请恢复原钥匙串或重新配置密钥。") from exc

    def resolve(self, ciphertext: str | None, reference: str | None) -> str | None:
        if ciphertext:
            return self.decrypt(ciphertext)
        if not reference:
            return None
        prefix, separator, variable = reference.partition(":")
        if prefix != "env" or not separator or not variable:
            raise ValueError("凭据引用只支持 env:VARIABLE 格式。")
        return os.getenv(variable)
