import pathlib
p = pathlib.Path("tests/test_phase5_cli.py")
t = p.read_text(encoding="utf-8")
# Replace existing imports with the augmented set
marker = "from relay.cli import build_parser"
if marker in t and "import tempfile" not in t:
    # Prepend imports the new tests need
    insertion = "import socket\nimport tempfile\nimport threading\nfrom pathlib import Path\n\nfrom relay.config import Config\nfrom relay.daemon import RelayDaemon\nfrom relay.errors import RelayError\nfrom relay.rpc import RPCClient\n\n"
    t = t.replace(marker, insertion + marker, 1)
    p.write_text(t, encoding="utf-8")
    print("updated imports")
else:
    print("skipped")
