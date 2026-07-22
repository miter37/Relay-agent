import json
from pathlib import Path

from .model_catalog import ModelCatalog, DiscoveredModel
from .util import utc_now


def parse_agy_models(text: str) -> list[str]:
    models: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.removeprefix("*").removeprefix("-").strip()
        if line and not line.lower().startswith(("available models", "models:")):
            models.append(line)
    return list(dict.fromkeys(models))


import subprocess
import time
from typing import Any

def list_codex_models(
    executable: str = "codex",
    timeout_seconds: float = 20.0,
    include_hidden: bool = False,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if process.stdin is None or process.stdout is None:
        process.kill()
        raise RuntimeError("Failed to open Codex app-server pipes")

    def send(message: dict[str, Any]) -> None:
        process.stdin.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        process.stdin.flush()

    try:
        send({
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "relay",
                    "title": "Relay",
                    "version": "0.6.0",
                }
            },
        })

        initialized = False
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    raise RuntimeError("Codex app-server exited during initialization")
                continue

            message = json.loads(line)

            if message.get("id") == 1:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                initialized = True
                break

        if not initialized:
            raise TimeoutError("Codex app-server initialization timed out")

        send({"method": "initialized", "params": {}})
        send({
            "method": "model/list",
            "id": 2,
            "params": {
                "limit": 100,
                "cursor": None,
                "includeHidden": include_hidden,
            },
        })

        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    raise RuntimeError("Codex app-server exited before model response")
                continue

            message = json.loads(line)

            if message.get("id") != 2:
                continue

            if "error" in message:
                raise RuntimeError(str(message["error"]))

            return message["result"]

        raise TimeoutError("Codex model/list timed out")

    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


def parse_claude_settings() -> list[str]:
    home = Path.home()
    candidates = [
        home / ".claude.json",
        home / ".claude" / "settings.json",
    ]
    models = set()
    for path in candidates:
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Try to extract models from common settings
                if "model" in data and isinstance(data["model"], str):
                    models.add(data["model"])
                if "availableModels" in data and isinstance(data["availableModels"], list):
                    models.update(data["availableModels"])
                
                # Simple heuristical extraction just to get *something*
                for k, v in data.items():
                    if "model" in k.lower() and isinstance(v, str) and v.startswith("claude-"):
                        models.add(v)
            except Exception:
                pass
                
    # Add common aliases
    models.update(["claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-5-haiku-20241022", "sonnet", "opus", "haiku"])
    return list(models)

def probe_claude_model(executable: str, model: str) -> bool:
    try:
        process = subprocess.run(
            [executable, "-p", "Reply exactly MODEL_OK", "--model", model, "--max-turns", "1", "--output-format", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        return "MODEL_OK" in process.stdout or process.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False

def get_model_catalog(config: Any, adapter: Any, refresh: bool = False, include_hidden: bool = False, verify: bool = False) -> Any:
    # We load the json from config.path_value("model_catalogs_root")
    from .util import json_dump, json_load
    
    catalog_dir = config.path_value("model_catalogs_root") if config.get("model_catalogs_root") else config.home / "model-catalogs"
    worker_dir = catalog_dir / adapter.name
    worker_dir.mkdir(parents=True, exist_ok=True)
    
    version = adapter.version() or "unknown"
    import re
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", version)[:120]
    cache_file = worker_dir / f"{safe_version}.json"
    
    import time
    if not refresh and cache_file.exists():
        try:
            cached = json_load(cache_file)
            # check ttl 30 minutes
            if cached and cached.get("generated_at"):
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(cached["generated_at"])
                if (datetime.now(timezone.utc) - dt).total_seconds() < 1800:
                    from .model_catalog import ModelCatalog, DiscoveredModel
                    models = [DiscoveredModel(**m) for m in cached.get("models", [])]
                    cached["models"] = models
                    # If we don't need to verify, or it's already verified
                    return ModelCatalog(**{k:v for k,v in cached.items() if k != "generated_at"})
        except Exception:
            pass

    catalog = adapter.discover_models(refresh=refresh, include_hidden=include_hidden, verify=verify)
    
    # Save cache
    data = catalog.to_dict()
    from .util import utc_now
    data["generated_at"] = utc_now()
    json_dump(cache_file, data)
    
    return catalog

