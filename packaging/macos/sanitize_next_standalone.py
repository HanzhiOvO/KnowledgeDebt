from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove build-machine paths from a copied Next.js standalone runtime."
    )
    parser.add_argument("--web-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()

    web_root = args.web_root.resolve()
    source_root = str(args.source_root.resolve())
    targets = [web_root / "server.js", web_root / ".next" / "required-server-files.json"]
    for target in targets:
        content = target.read_text(encoding="utf-8")
        sanitized = content.replace(source_root, ".")
        if source_root in sanitized:
            raise RuntimeError(f"未能清除 Next.js 构建路径：{target}")
        target.write_text(sanitized, encoding="utf-8")

    # Prove the JSON artifact remains valid after replacement. server.js is exercised by smoke_test.sh.
    json.loads(targets[1].read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
