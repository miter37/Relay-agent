"""Task Interface normalization and graph connection diagnostics.

The first version deliberately sits beside the legacy Task/Project storage
model.  Existing Tasks keep ``input_schema`` and existing Projects keep
``from_role``/``to_alias``.  This module gives every caller one readable view
of those definitions and one structured set of diagnostics while the newer
named-port representation is introduced incrementally.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .task_inputs import parse_schema

INTERFACE_VERSION = 1
_CARDINALITIES = {"one", "many"}
_SYSTEM_OUTPUT_ROLE = "result"


@dataclass(frozen=True, slots=True)
class InterfacePort:
    """A named Artifact input or output exposed by a Task."""

    name: str
    required: bool = False
    cardinality: str = "one"
    formats: tuple[str, ...] = ()
    description: str | None = None
    system: bool = False

    def to_dict(self, *, output: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name" if not output else "role": self.name,
            "required": self.required,
            "cardinality": self.cardinality,
        }
        if self.formats:
            result["accepts" if not output else "produces"] = list(self.formats)
        if self.description:
            result["description"] = self.description
        if self.system:
            result["system"] = True
        return result


@dataclass(frozen=True, slots=True)
class TaskInterface:
    """Normalized, read-only view of a Task's complete interface."""

    parameters: dict[str, Any] | None
    artifact_inputs: tuple[InterfacePort, ...]
    outputs: tuple[InterfacePort, ...]
    declared: bool
    source: str
    interface_version: int = INTERFACE_VERSION

    def input(self, name: str) -> InterfacePort | None:
        return next((port for port in self.artifact_inputs if port.name == name), None)

    def output(self, name: str) -> InterfacePort | None:
        return next((port for port in self.outputs if port.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface_version": self.interface_version,
            "parameters": self.parameters,
            "artifact_inputs": [port.to_dict() for port in self.artifact_inputs],
            "outputs": [port.to_dict(output=True) for port in self.outputs],
            "declared": self.declared,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class InterfaceDiagnostic:
    code: str
    severity: str
    message: str
    path: str = ""
    suggestions: tuple[str, ...] = ()
    node_id: str | None = None
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "suggestions": list(self.suggestions),
            "node_id": self.node_id,
            "blocking": self.blocking,
        }


@dataclass(slots=True)
class InterfaceValidationReport:
    diagnostics: list[InterfaceDiagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[InterfaceDiagnostic]:
        return [item for item in self.diagnostics if item.severity == "error"]

    @property
    def warnings(self) -> list[InterfaceDiagnostic]:
        return [item for item in self.diagnostics if item.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


def diagnose_output_artifacts(
    task: Mapping[str, Any],
    artifact_records: Sequence[Mapping[str, Any]],
) -> InterfaceValidationReport:
    """Check a completed Task's produced Artifacts against declared Outputs."""

    report = InterfaceValidationReport()
    try:
        interface = task_interface(task)
    except (TypeError, ValueError) as exc:
        report.diagnostics.append(
            InterfaceDiagnostic(
                code="TASK_INTERFACE_INVALID",
                severity="error",
                message=str(exc),
                blocking=True,
            )
        )
        return report
    if not interface.declared:
        return report
    records = [item for item in artifact_records if isinstance(item, Mapping)]
    for port in interface.outputs:
        if port.system:
            continue
        matches = [item for item in records if str(item.get("role") or "") == port.name]
        if port.required and not matches:
            report.diagnostics.append(
                InterfaceDiagnostic(
                    code="OUTPUT_CONTRACT_MISSING",
                    severity="error",
                    message=f"Required Output {port.name!r} was not produced.",
                    path=f"/outputs/{port.name}",
                    node_id=port.name,
                    blocking=True,
                )
            )
            continue
        if port.cardinality == "one" and len(matches) > 1:
            report.diagnostics.append(
                InterfaceDiagnostic(
                    code="OUTPUT_CONTRACT_CARDINALITY",
                    severity="error",
                    message=f"Output {port.name!r} produced {len(matches)} files; exactly one is required.",
                    path=f"/outputs/{port.name}",
                    node_id=port.name,
                    blocking=True,
                )
            )
        for item in matches:
            mime = str(item.get("mime_type") or "application/octet-stream")
            compatibility = _format_compatible((mime,), port.formats)
            if compatibility is False:
                report.diagnostics.append(
                    InterfaceDiagnostic(
                        code="OUTPUT_CONTRACT_FORMAT",
                        severity="error",
                        message=f"Output {port.name!r} has format {mime}, not one of {', '.join(port.formats)}.",
                        path=f"/outputs/{port.name}",
                        suggestions=port.formats,
                        node_id=port.name,
                        blocking=True,
                    )
                )
    return report


def _string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = tuple(value)
    else:
        raise ValueError(f"{field_name} must be a string or array of strings")
    if any(not isinstance(item, str) for item in values):
        raise ValueError(f"{field_name} must contain only strings")
    return tuple(item.strip() for item in values if item.strip())


def _port(value: Any, *, output: bool, index: int) -> InterfacePort:
    if not isinstance(value, Mapping):
        raise ValueError(f"{'output' if output else 'artifact input'} {index} must be an object")
    key = "role" if output else "name"
    raw_name = value.get(key)
    if not isinstance(raw_name, str):
        raise ValueError(f"{key} must be a string for {'output' if output else 'artifact input'} {index}")
    name = raw_name.strip()
    if not name:
        raise ValueError(f"{key} is required for {'output' if output else 'artifact input'} {index}")
    raw_cardinality = value.get("cardinality", "one")
    if not isinstance(raw_cardinality, str):
        raise ValueError(f"{key} {name!r} cardinality must be one or many")
    cardinality = raw_cardinality.strip().casefold()
    if cardinality not in _CARDINALITIES:
        raise ValueError(f"{key} {name!r} cardinality must be one or many")
    formats = _string_list(value.get("produces" if output else "accepts"), field_name=key)
    raw_description = value.get("description")
    if raw_description is not None and not isinstance(raw_description, str):
        raise ValueError(f"{key} {name!r} description must be a string")
    description = (raw_description or "").strip() or None
    required = value.get("required", False)
    if not isinstance(required, bool):
        raise ValueError(f"{key} {name!r} required must be a boolean")
    return InterfacePort(
        name=name,
        required=required,
        cardinality=cardinality,
        formats=formats,
        description=description,
        system=bool(value.get("system", False)),
    )


def _contract_payload(raw: Any) -> tuple[dict[str, Any] | None, str]:
    if raw in (None, ""):
        return None, "legacy"
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"output_contract is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("output_contract must be a JSON object")
    if payload.get("interface_version") != INTERFACE_VERSION:
        return None, "legacy"
    return payload, "interface"


def task_interface(task: Mapping[str, Any]) -> TaskInterface:
    """Return the normalized interface for a stored Task mapping.

    ``result`` is always exposed as a system Output because Relay materializes
    that result file for every successful Task Run.  An absent or unrecognized
    contract remains a valid Legacy interface with a warning at graph level.
    """

    parameters: dict[str, Any] | None = None
    raw_parameters = task.get("input_schema")
    if raw_parameters not in (None, ""):
        if isinstance(raw_parameters, Mapping):
            parameters = dict(raw_parameters)
        else:
            parameters = parse_schema(str(raw_parameters))
    payload, source = _contract_payload(task.get("output_contract"))
    artifact_inputs: tuple[InterfacePort, ...] = ()
    declared_outputs: tuple[InterfacePort, ...] = ()
    if payload is not None:
        artifact_inputs = tuple(
            _port(value, output=False, index=index)
            for index, value in enumerate(payload.get("artifact_inputs") or [], start=1)
        )
        declared_outputs = tuple(
            _port(value, output=True, index=index)
            for index, value in enumerate(payload.get("outputs") or [], start=1)
        )
    result_format = str(task.get("result_format") or "json").casefold()
    result_mime = "application/json" if result_format == "json" else "text/plain"
    result_port = InterfacePort(
        name=_SYSTEM_OUTPUT_ROLE,
        required=True,
        cardinality="one",
        formats=(result_mime,),
        description="Relay's primary Task result.",
        system=True,
    )
    outputs = (result_port, *tuple(port for port in declared_outputs if port.name != _SYSTEM_OUTPUT_ROLE))
    return TaskInterface(
        parameters=parameters,
        artifact_inputs=artifact_inputs,
        outputs=outputs,
        declared=payload is not None,
        source=source,
    )


def normalize_interface(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a new Interface payload before persistence or transport."""

    if payload.get("interface_version", INTERFACE_VERSION) != INTERFACE_VERSION:
        raise ValueError(f"interface_version must be {INTERFACE_VERSION}")
    inputs = tuple(
        _port(value, output=False, index=index)
        for index, value in enumerate(payload.get("artifact_inputs") or [], start=1)
    )
    outputs = tuple(
        _port(value, output=True, index=index)
        for index, value in enumerate(payload.get("outputs") or [], start=1)
    )
    names = [port.name for port in inputs]
    if len(names) != len(set(names)):
        raise ValueError("artifact input names must be unique")
    roles = [port.name for port in outputs]
    if len(roles) != len(set(roles)):
        raise ValueError("output roles must be unique")
    if _SYSTEM_OUTPUT_ROLE in roles:
        raise ValueError("result is reserved for Relay and must not be declared")
    return {
        "interface_version": INTERFACE_VERSION,
        "artifact_inputs": [port.to_dict() for port in inputs],
        "outputs": [port.to_dict(output=True) for port in outputs],
    }


def _format_compatible(produces: Sequence[str], accepts: Sequence[str]) -> bool | None:
    """Return True/False, or None when either side is intentionally open."""

    if not produces or not accepts:
        return None
    normalized_produces = {str(item).casefold() for item in produces}
    normalized_accepts = {str(item).casefold() for item in accepts}
    if "*/*" in normalized_accepts or "any" in normalized_accepts or "any file" in normalized_accepts:
        return True
    if normalized_produces & normalized_accepts:
        return True
    if "application/json" in normalized_produces and "json" in normalized_accepts:
        return True
    if "text/plain" in normalized_produces and {"text", "document"} & normalized_accepts:
        return True
    if any(item.startswith("text/") for item in normalized_produces) and "document" in normalized_accepts:
        return True
    return False


def diagnose_project_interfaces(
    definition: Mapping[str, Any],
    task_lookup: Callable[[str], Mapping[str, Any] | None],
) -> InterfaceValidationReport:
    """Diagnose named-port connections and legacy bindings without mutating data."""

    report = InterfaceValidationReport()
    nodes = definition.get("nodes") or []
    node_interfaces: dict[str, TaskInterface] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "").strip()
        task_id = str(node.get("task_id") or "").strip()
        task = task_lookup(task_id) if task_id else None
        if task is None:
            continue
        try:
            node_interfaces[node_id] = task_interface(task)
        except (TypeError, ValueError) as exc:
            report.diagnostics.append(
                InterfaceDiagnostic(
                    code="TASK_INTERFACE_INVALID",
                    severity="error",
                    message=f"Task interface for node {node_id!r} is invalid: {exc}",
                    path=f"/nodes/{index}",
                    node_id=node_id,
                    blocking=True,
                )
            )

    connections = definition.get("connections") or []
    for index, connection in enumerate(connections):
        if not isinstance(connection, Mapping):
            continue
        from_node = str(connection.get("from_node") or "").strip()
        to_node = str(connection.get("to_node") or "").strip()
        from_role = str(connection.get("from_output") or connection.get("from_role") or "").strip()
        to_input = str(connection.get("to_input") or "").strip()
        to_alias = str(connection.get("to_alias") or "").strip()
        source = node_interfaces.get(from_node)
        target = node_interfaces.get(to_node)
        path = f"/connections/{index}"
        if source and from_role and source.declared and source.output(from_role) is None:
            report.diagnostics.append(
                InterfaceDiagnostic(
                    code="CONNECTION_OUTPUT_UNKNOWN",
                    severity="error",
                    message=f"Output {from_role!r} is not declared by node {from_node!r}.",
                    path=f"{path}/from_output" if connection.get("from_output") else f"{path}/from_role",
                    suggestions=tuple(port.name for port in source.outputs),
                    node_id=from_node,
                    blocking=True,
                )
            )
        elif source and not source.declared:
            report.diagnostics.append(
                InterfaceDiagnostic(
                    code="CONNECTION_OUTPUT_UNDECLARED",
                    severity="warning",
                    message=f"Node {from_node!r} has no declared Outputs; this connection will be checked at runtime.",
                    path=f"{path}/from_role",
                    suggestions=("result",),
                    node_id=from_node,
                )
            )
        if target and to_input:
            destination = target.input(to_input)
            if target.declared and destination is None:
                report.diagnostics.append(
                    InterfaceDiagnostic(
                        code="CONNECTION_INPUT_UNKNOWN",
                        severity="error",
                        message=f"Artifact input {to_input!r} is not declared by node {to_node!r}.",
                        path=f"{path}/to_input",
                        suggestions=tuple(port.name for port in target.artifact_inputs),
                        node_id=to_node,
                        blocking=True,
                    )
                )
            if destination and source and source.output(from_role):
                compatibility = _format_compatible(
                    source.output(from_role).formats,
                    destination.formats,
                )
                if compatibility is False:
                    report.diagnostics.append(
                        InterfaceDiagnostic(
                            code="CONNECTION_FORMAT_INCOMPATIBLE",
                            severity="error",
                            message=(
                                f"Output {from_node}.{from_role} does not satisfy "
                                f"input {to_node}.{to_input}."
                            ),
                            path=path,
                            suggestions=destination.formats,
                            node_id=to_node,
                            blocking=True,
                        )
                    )
                elif compatibility is None:
                    report.diagnostics.append(
                        InterfaceDiagnostic(
                            code="CONNECTION_FORMAT_UNSPECIFIED",
                            severity="warning",
                            message="The connection can run, but one side does not declare an accepted format.",
                            path=path,
                            node_id=to_node,
                        )
                    )
        elif target and to_alias and target.declared:
            report.diagnostics.append(
                InterfaceDiagnostic(
                    code="CONNECTION_LEGACY_ALIAS",
                    severity="warning",
                    message=(
                        f"Legacy input alias {to_alias!r} is still supported; choose a named Artifact input "
                        f"for node {to_node!r} when editing this connection."
                    ),
                    path=f"{path}/to_alias",
                    suggestions=tuple(port.name for port in target.artifact_inputs),
                    node_id=to_node,
                )
            )
    return report
