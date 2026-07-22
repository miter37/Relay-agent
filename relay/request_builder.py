from __future__ import annotations

import json
import shutil
from pathlib import Path

from .errors import RelayError
from .models import JobRequest
from .util import ensure_dir, sha256_file


STANDARD_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "status", "answer", "sources", "uncertainties", "missing_items", "artifacts"],
    "properties": {
        "schema_version": {"type": "string"},
        "status": {"type": "string", "enum": ["complete", "partial", "failed"]},
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "missing_items": {"type": "array", "items": {"type": "string"}},
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["relative_path", "description", "encoding", "content"],
                "properties": {
                    "relative_path": {"type": "string"},
                    "description": {"type": "string"},
                    "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                    "content": {"type": "string"},
                },
            },
        },
    },
}


def write_schema(path: Path) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(STANDARD_JSON_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_attachments(request: JobRequest, input_dir: Path) -> list[dict]:
    ensure_dir(input_dir)
    copied: list[dict] = []
    names: set[str] = set()
    for source_text in request.attachments:
        source = Path(source_text).expanduser().resolve()
        if not source.is_file():
            raise RelayError("ATTACHMENT_NOT_FOUND", f"Attachment not found: {source}")
        name = source.name
        stem, suffix = source.stem, source.suffix
        index = 2
        while name.lower() in names:
            name = f"{stem}_{index}{suffix}"
            index += 1
        names.add(name.lower())
        target = input_dir / name
        shutil.copy2(source, target)
        copied.append({"name": name, "path": str(target), "sha256": sha256_file(target), "size": target.stat().st_size})
    return copied


def build_request_markdown(
    request: JobRequest,
    result_file: Path,
    artifact_dir: Path,
    attachments: list[dict],
) -> str:
    format_rules = (
        "Return a UTF-8 JSON object matching schema.json exactly. Do not wrap it in Markdown fences.\n"
        "- For every requested artifact, include an artifacts entry with relative_path, description, encoding, "
        "and exact content. Use encoding=utf-8 for text and encoding=base64 for binary content. Relay "
        "materializes this payload into the artifact directory, so a valid payload is sufficient to complete "
        "the artifact request. You may also create the file directly. relative_path is relative to the artifact "
        "directory; do not prefix it with artifacts/."
        if request.result_format == "json"
        else "Return a non-empty UTF-8 plain-text result."
    )
    attachment_lines = "\n".join(f"- `{item['name']}` at `input/{item['name']}`" for item in attachments) or "- None"
    profile_rules = {
        "web-research": (
            "- Use current web sources where available.\n"
            "- Include source URLs for material claims.\n"
            "- Separate confirmed facts from estimates or interpretation.\n"
            "- Put unresolved issues in uncertainties or missing_items."
        ),
        "analysis-only": "- Do not modify input files.\n- Produce analysis only.",
        "general-artifact": "- Produce the requested result and any requested supporting artifacts.",
    }.get(request.profile, "- Complete the requested task faithfully.")
    return f"""# Relay Task

## Execution Contract
- This is a non-interactive run. Do not ask the user questions or wait for approval.
- Make reasonable assumptions where necessary and disclose them in the result.
- Work only inside this workspace.
- Final result target: `{result_file}`
- Artifact directory: `{artifact_dir}`
- {format_rules}
- Do not claim completion if required work could not be completed.

## Profile
{profile_rules}

## Input Attachments
{attachment_lines}

## User Task
{request.task.strip()}
"""
