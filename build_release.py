from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "relay.pyz"

with tempfile.TemporaryDirectory() as tmp:
    stage = Path(tmp)
    shutil.copytree(ROOT / "relay", stage / "relay")
    zipapp.create_archive(stage, TARGET, interpreter="/usr/bin/env python3", main="relay.cli:main", compressed=True)

print(TARGET)
