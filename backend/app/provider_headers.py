from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_BLOCKED_HEADERS = {
    "api-key",
    "authorization",
    "connection",
    "content-length",
    "content-type",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "transfer-encoding",
    "upgrade",
    "x-api-key",
    "x-auth-token",
}


def validate_custom_headers(value: Mapping[str, str] | None) -> dict[str, str]:
    """Validate non-sensitive OpenAI-compatible request headers.

    Authentication stays in the encrypted credential field. Keeping auth-like headers out of
    this mapping prevents accidentally returning secrets through the public Profile API.
    """

    if not value:
        return {}
    if len(value) > 20:
        raise ValueError("自定义请求头最多允许 20 项。")

    result: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError("自定义请求头的名称和值都必须是字符串。")
        name = raw_name.strip()
        header_value = raw_value.strip()
        normalized = name.lower()
        if not name or len(name) > 120 or not _HEADER_NAME.fullmatch(name):
            raise ValueError(f"自定义请求头名称“{raw_name}”不合法。")
        if normalized in _BLOCKED_HEADERS or normalized.startswith("proxy-"):
            raise ValueError(
                f"请求头“{name}”可能包含凭据或会干扰 HTTP 请求；请改用加密的 API Key 字段。"
            )
        if normalized in seen:
            raise ValueError(f"自定义请求头“{name}”重复。")
        if not header_value:
            raise ValueError(f"自定义请求头“{name}”的值不能为空。")
        if len(header_value) > 2000:
            raise ValueError(f"自定义请求头“{name}”的值过长。")
        if "\r" in header_value or "\n" in header_value:
            raise ValueError(f"自定义请求头“{name}”不能包含换行符。")
        result[name] = header_value
        seen.add(normalized)
    return result


def normalize_external_base_url(value: str) -> str:
    """Accept only credential-free HTTPS API roots for external providers."""

    candidate = value.strip().rstrip("/")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL 格式无效，请填写完整的 HTTPS API 地址。") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("外部 Provider 的 Base URL 必须是完整的 HTTPS 地址。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Base URL 不能包含用户名或密码；请使用加密的 API Key 字段。")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含查询参数或片段，避免把 Token 写入普通配置。")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Base URL 端口必须在 1 到 65535 之间。")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))
