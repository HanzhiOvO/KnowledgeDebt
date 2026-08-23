from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path


def python_inventory(target: Path) -> None:
    rows: list[tuple[str, str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name") or "unknown"
        license_name = distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or "未声明"
        rows.append((name, distribution.version, " ".join(license_name.split())))
    lines = ["名称\t版本\t许可证（来自包元数据）"]
    lines.extend("\t".join(row) for row in sorted(set(rows), key=lambda item: item[0].lower()))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def node_inventory(root: Path, target: Path) -> None:
    rows: set[tuple[str, str, str]] = set()
    for package in root.glob("**/package.json"):
        if "node_modules" not in package.parts:
            continue
        try:
            payload = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        name = payload.get("name")
        version = payload.get("version")
        if not name or not version:
            continue
        license_value = payload.get("license", "未声明")
        if isinstance(license_value, dict):
            license_value = license_value.get("type", "未声明")
        rows.add((str(name), str(version), str(license_value)))
    lines = ["名称\t版本\t许可证（来自 package.json）"]
    lines.extend("\t".join(row) for row in sorted(rows, key=lambda item: item[0].lower()))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    python_inventory(args.output / "PYTHON_PACKAGES.txt")
    node_inventory(args.web_root, args.output / "NODE_PACKAGES.txt")


if __name__ == "__main__":
    main()
