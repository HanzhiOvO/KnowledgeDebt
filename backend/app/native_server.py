from __future__ import annotations

import os

import uvicorn

from .config import Settings
from .main import create_app
from .native_runtime import backup_database_before_upgrade, prepare_native_data_directory


def main() -> None:
    settings = Settings.from_env()
    data_dir = prepare_native_data_directory(settings.data_dir)
    backup_database_before_upgrade(
        data_dir,
        os.getenv("KNOWLEDGEDEBT_APP_VERSION", "0.2.0"),
    )
    port = int(os.getenv("KNOWLEDGEDEBT_API_PORT", "8123"))
    if not 1 <= port <= 65535:
        raise ValueError("本地 API 端口必须在 1 到 65535 之间。")
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        log_level=os.getenv("KNOWLEDGEDEBT_LOG_LEVEL", "info"),
        access_log=False,
    )


if __name__ == "__main__":
    main()
