from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "relay.pyz"
CHECKSUMS = ROOT / "SHA256SUMS.txt"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        shutil.copytree(
            ROOT / "relay",
            stage / "relay",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        zipapp.create_archive(stage, TARGET, interpreter="/usr/bin/env python3", main="relay.cli:main", compressed=True)
    return TARGET


def write_checksums(*paths: Path) -> Path:
    lines = [f"{sha256_of(path)}  {path.name}" for path in paths if path.is_file()]
    CHECKSUMS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return CHECKSUMS


if __name__ == "__main__":
    artifact = build()
    checksums = write_checksums(artifact)
    print(artifact)
    print(checksums)
    print(checksums.read_text(encoding="utf-8").strip())
