from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .adapters.generic import (
    validate_command_template,
    validate_worker_id,
)
from .cleanup import CleanupManager
from .config import Config
from .daemon import RelayDaemon
from .db import Database
from .doctor import Doctor
from .engine import RelayEngine
from .errors import RelayError
from .models import JobRequest
from .rpc import RPCClient
from .security import security_posture, set_full_access_mode
from .util import entrypoint_command, utc_now

COMMANDS = {
    "run",
    "submit",
    "status",
    "wait",
    "result",
    "show",
    "logs",
    "cancel",
    "history",
    "rerun",
    "doctor",
    "config",
    "cleanup",
    "daemon",
    "version",
    "init",
    "security",
    "models",
    "model-check",
    "add-agent",
    "schedule",
}


def _preprocess(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] not in COMMANDS and not argv[0].startswith("-"):
        return ["run", *argv]
    return argv


def _add_request_args(parser: argparse.ArgumentParser, task_required: bool = False) -> None:
    parser.add_argument("task", nargs=None if task_required else "?", default="")
    parser.add_argument("--title", help="Optional short title shown in job history")
    parser.add_argument("--task-file")
    parser.add_argument("--worker", default="auto", help="Agent ID from the built-in or custom Agent registry")
    parser.add_argument("--fallback", action="store_true", default=None)
    parser.add_argument("--no-fallback", action="store_false", dest="fallback")
    parser.add_argument("--fallback-agent", action="append", default=None, dest="fallback_agents")
    parser.add_argument("--format", dest="result_format", choices=["json", "txt"])
    parser.add_argument("--out", dest="output_path")
    parser.add_argument("--artifacts", dest="artifact_path")
    parser.add_argument("--profile")
    parser.add_argument("--timeout", dest="timeout_seconds", type=int)
    parser.add_argument("--caller", default="human")
    parser.add_argument("--request-id")
    parser.add_argument("--attach", action="append", default=[], dest="attachments")
    parser.add_argument("--workspace")
    parser.add_argument(
        "--target",
        dest="target_path",
        help="Real folder to create or modify; changed files are also copied to --artifacts",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--force-new", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--machine", action="store_true")


def _add_schedule_rule_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--type", dest="rule_type", required=True, choices=["daily", "weekly", "monthly", "n_days", "once"]
    )
    parser.add_argument("--time", dest="times", action="append", default=[], help="Local time (repeatable, HH:MM)")
    parser.add_argument(
        "--weekday", dest="weekdays", action="append", type=int, default=[], help="ISO weekday 1-7 (repeatable)"
    )
    parser.add_argument(
        "--month-day", dest="month_days", action="append", type=int, default=[], help="Month day 1-31 (repeatable)"
    )
    parser.add_argument("--missing-month-day", dest="missing_day_policy", choices=["skip", "last_day"], default="skip")
    parser.add_argument("--interval-days", type=int)
    parser.add_argument("--anchor-date")
    parser.add_argument("--run-at-local")
    parser.add_argument("--timezone", default="UTC", help="IANA timezone (default: UTC)")


def _add_schedule_machine_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--machine", action="store_true", help="Emit single-line JSON output")


def _add_schedule_policy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--overlap-policy", choices=["skip", "queue"], default="skip")
    parser.add_argument("--missed-policy", choices=["skip", "catch_up"], default="skip")
    parser.add_argument("--missed-grace-seconds", type=int, default=43200)
    parser.add_argument("--start", "--starts-at-utc", dest="starts_at_utc")
    parser.add_argument("--end", "--ends-at-utc", dest="ends_at_utc")
    parser.add_argument("--output-root")
    parser.add_argument("--retention-mode", choices=["days", "latest_runs", "forever"])
    parser.add_argument("--retention-value", type=int)


def _add_schedule_parsers(sub: argparse._SubParsersAction) -> None:
    schedule = sub.add_parser(
        "schedule",
        help="Create and control daemon-managed schedules",
        description=(
            "Register a replayable completed Job as a timezone-aware Schedule, preview occurrences, "
            "and control its lifecycle. Schedule execution is performed by the local daemon."
        ),
        epilog=(
            "Examples:\n"
            "  relay schedule create --from-job JOB_ID --name report --type daily --time 09:00 --timezone Asia/Seoul\n"
            "  relay schedule preview --type weekly --weekday 1 --time 09:00 --timezone Asia/Seoul\n"
            "  relay schedule run-now SCHEDULE_ID"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)

    create = schedule_sub.add_parser("create", help="Create a Schedule from a completed replayable Job")
    create.add_argument("--from-job", dest="source_job_id", required=True)
    create.add_argument("--name", required=True)
    _add_schedule_rule_args(create)
    _add_schedule_policy_args(create)
    _add_schedule_machine_arg(create)

    preview = schedule_sub.add_parser("preview", help="Preview the next Schedule occurrences")
    _add_schedule_rule_args(preview)
    preview.add_argument("--limit", type=int, default=5)
    preview.add_argument("--after-utc")
    _add_schedule_machine_arg(preview)

    list_parser = schedule_sub.add_parser("list", help="List active Schedules")
    _add_schedule_machine_arg(list_parser)

    for name, help_text in (
        ("show", "Show one Schedule"),
        ("runs", "List Schedule run history"),
        ("pause", "Pause a Schedule"),
        ("resume", "Resume a Schedule"),
        ("run-now", "Queue a manual Schedule run"),
        ("delete", "Soft-delete a Schedule"),
    ):
        action = schedule_sub.add_parser(name, help=help_text)
        action.add_argument("schedule_id")
        _add_schedule_machine_arg(action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relay",
        description=(
            "Reliable delegation broker for AI CLIs. "
            "Submits tasks to Claude, Codex, Antigravity, or any user-registered agent CLI, "
            "deduplicates by request_id, runs capability audits, and isolates work under RELAY_HOME. "
            "Use 'relay <command> --help' for command details."
        ),
        epilog=(
            "Quick start:\n"
            "  relay init\n"
            "  relay doctor --deep --worker claude\n"
            '  relay run "Summarize today\'s AI headlines"\n'
            "\n"
            "Common commands:\n"
            "  run, submit, status, wait, result, cancel, rerun\n"
            "  doctor, config, security, cleanup, daemon\n"
            "  schedule      Create and control daemon-managed schedules\n"
            "  add-agent      Register a new external AI CLI as a worker\n"
            "  models, model-check, history, logs, version, init"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"Relay {__version__}")
    parser.add_argument("--home", help="Use this Relay Home directory")
    parser.add_argument("--gui", action="store_true", help="Open the optional read-only desktop GUI")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser(
        "run",
        help="Run a task synchronously",
        description=(
            "Run a task synchronously and return the final receipt as JSON. "
            "Use this for one-off queries where you need the result inline. "
            "For background jobs that should survive shell exit, use 'relay submit'. "
            "The selected worker (claude, codex, antigravity, or any registered agent) "
            "executes the task in a sandbox under RELAY_HOME; fallback workers run if enabled."
        ),
        epilog=(
            "Examples:\n"
            '  relay run "Summarize today\'s AI headlines"\n'
            '  relay run --worker codex --no-fallback "Convert CSV to JSON"\n'
            '  relay run --profile web-research --format json "Research X"\n'
            '  relay run --request-id chat-42 --force-new "Re-run identical request"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_request_args(run)

    submit = sub.add_parser(
        "submit",
        help="Submit a background task to the daemon",
        description=(
            "Submit a task to the daemon and queue it for background execution. "
            "The daemon is started automatically if it is not running. "
            "Use 'relay wait <job_id>' or 'relay status <job_id>' to monitor progress, "
            "and 'relay result <job_id>' to retrieve the final receipt."
        ),
        epilog=(
            "Examples:\n"
            '  relay submit "Research OpenAI\'s latest announcements"\n'
            "  relay submit --worker claude --request-id msg-42 --machine\n"
            '  relay submit --attach spec.pdf --out result.json "Summarize spec"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_request_args(submit)

    for name in ("status", "result", "show", "logs", "cancel", "rerun"):
        p = sub.add_parser(
            name,
            description=(
                {
                    "status": "Return the current status of a job. Uses the daemon when available, otherwise reads the local database.",
                    "result": "Return the final receipt and result/artifact paths of a completed job.",
                    "show": "Return detailed local job data including attempts, events, and artifacts.",
                    "logs": "Return attempt metadata and the tail of stdout/stderr logs (last 8,000 characters each).",
                    "cancel": "Request cancellation of a queued or running job. Submitted to the daemon when available.",
                    "rerun": "Reconstruct the saved request and execute it again as a new job.",
                }[name]
            ),
            epilog=f"Examples:\n  relay {name} <job_id> --machine",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument("job_id")
        p.add_argument("--machine", action="store_true")

    wait = sub.add_parser(
        "wait",
        help="Block until a job completes",
        description=(
            "Poll a job until it reaches a terminal state (completed, partial, failed, or cancelled) "
            "or until the timeout expires. Returns the final receipt. "
            "Use this from scripts that need to chain work after the job is done."
        ),
        epilog=(
            "Examples:\n"
            "  relay wait <job_id> --timeout 1800\n"
            "  relay wait <job_id> --timeout 60 --interval 0.5 --machine"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=int, default=0)
    wait.add_argument("--interval", type=float, default=2.0)
    wait.add_argument("--machine", action="store_true")

    history = sub.add_parser(
        "history",
        help="List recent jobs",
        description=(
            "List recent jobs from the local database, optionally filtered by status. "
            "Use 'relay status <job_id>' or 'relay show <job_id>' for details on a specific job."
        ),
        epilog=("Examples:\n  relay history --limit 20\n  relay history --status failed --machine"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    history.add_argument("--status")
    history.add_argument("--limit", type=int, default=50)
    history.add_argument("--machine", action="store_true")

    doctor = sub.add_parser(
        "doctor",
        help="Audit installed worker capability",
        description=(
            "Run shallow or deep capability audits for one or all installed workers. "
            "A shallow audit records version and help output. "
            "A deep audit additionally runs a sandboxed probe that must return a valid Relay result. "
            "Top-level ok=true only when every requested worker reaches status 'healthy'."
        ),
        epilog=("Examples:\n  relay doctor\n  relay doctor --worker claude --deep"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doctor.add_argument("--worker", help="Agent ID from the built-in or custom Agent registry")
    doctor.add_argument("--deep", action="store_true")
    doctor.add_argument("--machine", action="store_true")

    config = sub.add_parser(
        "config",
        help="View or edit relay configuration",
        description=(
            "Inspect and edit relay.toml under RELAY_HOME/config. "
            "Values passed to 'config set' are parsed as bool/int/float/list/string in that order. "
            "Worker enabling uses 'config enable-worker <name>' and requires a healthy adapter spec."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_sub = config.add_subparsers(dest="config_command", required=True)
    show_p = config_sub.add_parser(
        "show",
        help="Print the active configuration",
        description="Print the active configuration (merged defaults and relay.toml) and its file path.",
        epilog="Examples:\n  relay config show --machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument("--machine", action="store_true")
    set_p = config_sub.add_parser(
        "set",
        help="Set a configuration value",
        description="Set a single dotted key in relay.toml. Values are parsed as bool, list, int, float, or string.",
        epilog=(
            "Examples:\n"
            "  relay config set default_worker codex\n"
            "  relay config set workers.claude.max_turns 50\n"
            "  relay config set workers.antigravity.security_verified true"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("key")
    set_p.add_argument("value")
    set_p.add_argument("--machine", action="store_true")
    for action in ("enable-worker", "disable-worker"):
        p = config_sub.add_parser(
            action,
            help=f"{action.replace('-', ' ').capitalize()}",
            description=(
                "Enable or disable a worker. Enabling requires a healthy adapter spec for the "
                "currently installed CLI version (run 'relay doctor --deep --worker <name>' first). "
                "Disabling simply clears the 'enabled' flag."
            ),
            epilog=(f"Examples:\n  relay config {action} claude --machine"),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument("worker", choices=["claude", "codex", "antigravity"])
        p.add_argument("--machine", action="store_true")

    cleanup = sub.add_parser(
        "cleanup",
        help="Run or inspect automatic retention cleanup",
        description=(
            "Run a retention cleanup pass now, or inspect the configured policy and the last run. "
            "Without --dry-run this deletes eligible workspaces and staging directories immediately."
        ),
        epilog=("Examples:\n  relay cleanup --status\n  relay cleanup --dry-run --days 7"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cleanup.add_argument("--days", type=int)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--status", action="store_true", help="Show automatic cleanup policy and last run")
    cleanup.add_argument("--machine", action="store_true")

    daemon = sub.add_parser(
        "daemon",
        help="Run or query the local daemon",
        description="Run the local HTTP daemon in the foreground, or manage it as a detached process.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    daemon_sub = daemon.add_subparsers(dest="daemon_command", required=True)
    for name in ("serve", "start", "stop", "status"):
        dp = daemon_sub.add_parser(
            name,
            help=f"{name.capitalize()} the daemon",
            description={
                "serve": "Run the daemon HTTP server in the foreground (blocks until stopped).",
                "start": "Start a detached daemon process and log it under RELAY_HOME/runtime.",
                "stop": "Request the running daemon to shut down.",
                "status": "Query daemon health; reports 'stopped' if not running.",
            }[name],
            epilog=f"Examples:\n  relay daemon {name}",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        dp.add_argument("--machine", action="store_true")

    init = sub.add_parser(
        "init",
        help="Initialize RELAY_HOME and configuration",
        description="Create the configuration file and the standard RELAY_HOME directory tree. Use --force to rewrite an existing relay.toml.",
        epilog="Examples:\n  relay init --force",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    init.add_argument("--force", action="store_true")
    init.add_argument("--machine", action="store_true")

    security = sub.add_parser(
        "security",
        help="Show security posture",
        description="Print platform, isolation acknowledgement, allowlist roots, and security-related warnings.",
        epilog="Examples:\n  relay security --machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    security.add_argument("--machine", action="store_true")
    security.add_argument("--worker", choices=["claude", "codex", "antigravity"], help="Show one worker's mode")
    full_access = security.add_mutually_exclusive_group()
    full_access.add_argument(
        "--enable-full-access",
        metavar="WORKER",
        choices=["claude", "codex", "antigravity"],
        help="Enable that worker's permission/sandbox bypass",
    )
    full_access.add_argument(
        "--disable-full-access",
        metavar="WORKER",
        choices=["claude", "codex", "antigravity"],
        help="Disable that worker's permission/sandbox bypass",
    )

    models = sub.add_parser(
        "models",
        help="List models available to installed workers",
        description=(
            "List the models available to installed workers. "
            "Claude reads effective settings; Codex uses app-server model/list; Antigravity runs 'agy models'. "
            "Results are cached per worker CLI version for 30 minutes."
        ),
        epilog=(
            "Examples:\n"
            "  relay models\n"
            "  relay models --worker claude --refresh\n"
            "  relay models --worker all --include-hidden --verify"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    models.add_argument("--worker", default="all", help="Agent ID or all")
    models.add_argument("--refresh", action="store_true")
    models.add_argument("--include-hidden", action="store_true")
    models.add_argument("--verify", action="store_true")
    models.add_argument("--machine", action="store_true")

    model_check = sub.add_parser(
        "model-check",
        help="Verify that a worker can run a specific model",
        description=(
            "Verify that the named worker can run the requested model. "
            "Claude uses a minimal inference probe; Codex and Antigravity check the cached model catalog."
        ),
        epilog="Examples:\n  relay model-check --worker claude --model sonnet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    model_check.add_argument("--worker", required=True, help="Agent ID from the Agent registry")
    model_check.add_argument("--model", required=True)
    model_check.add_argument("--machine", action="store_true")

    add_agent = sub.add_parser(
        "add-agent",
        help="Interactively register a new external AI CLI as a Relay worker",
        description=(
            "Interactively register a new external AI CLI as a Relay worker. "
            "The wizard prompts for an ID, executable path, command template, default model, "
            "and optional advanced settings, then runs a health check (shallow + deep audit) "
            "before persisting the registration. If the health check fails, nothing is saved. "
            "Use --yes for non-interactive registration driven by environment variables."
        ),
        epilog=(
            "Examples:\n"
            "  relay add-agent\n"
            "  relay add-agent opencode\n"
            "  relay add-agent grok-build --yes\n"
            "  relay add-agent --skip-health-check my-agent\n"
            "\n"
            "Non-interactive mode (--yes) reads defaults from these environment variables:\n"
            "  RELAY_ADD_AGENT_ID\n"
            "  RELAY_ADD_AGENT_DISPLAY_NAME\n"
            "  RELAY_ADD_AGENT_COMMAND\n"
            "  RELAY_ADD_AGENT_COMMAND_TEMPLATE\n"
            "  RELAY_ADD_AGENT_DEFAULT_MODEL\n"
            "  RELAY_ADD_AGENT_REQUIRE_DEEP   (true/false, default: true)\n"
            "  RELAY_ADD_AGENT_ENABLE         (true/false, default: true)\n"
            "  RELAY_ADD_AGENT_DESCRIPTION    (optional)\n"
            "  RELAY_ADD_AGENT_EXTRA_ARGS     (optional, space-separated)\n"
            "  RELAY_ADD_AGENT_MAX_TURNS      (optional int)\n"
            "  RELAY_ADD_AGENT_TIMEOUT_SECONDS (optional int)\n"
            "\n"
            "Available placeholders in command_template:\n"
            "  {cli}, {request_file}, {result_file}, {artifact_dir}, {model}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_agent.add_argument(
        "worker_id",
        nargs="?",
        default=None,
        help="Worker ID used as the relay.toml key (lowercase letters, digits, '_' or '-'). If omitted, the wizard asks first.",
    )
    add_agent.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive mode. Use defaults or RELAY_ADD_AGENT_* environment variables for all prompts.",
    )
    add_agent.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip the health check and persist registration anyway. The worker remains unverified until 'relay doctor --deep --worker <id>' is run later.",
    )
    add_agent.add_argument(
        "--machine",
        action="store_true",
        help="Emit single-line JSON output (default: pretty-printed).",
    )

    agent_app = sub.add_parser(
        "agent-app",
        help="List and manage custom Agent Apps",
        description="Manage manifest-backed custom Agent Apps.",
    )
    agent_app_sub = agent_app.add_subparsers(dest="agent_app_command", required=True)
    agent_app_sub.add_parser("list", help="List custom Agent Apps")
    for name, help_text in (
        ("show", "Show an Agent App manifest"),
        ("test", "Run the deep Agent App test"),
        ("enable", "Enable an Agent App after a passing deep test"),
        ("disable", "Disable an Agent App"),
        ("delete", "Delete an Agent App"),
    ):
        command_parser = agent_app_sub.add_parser(name, help=help_text)
        command_parser.add_argument("agent_id")

    _add_schedule_parsers(sub)
    sub.add_parser("version", help="Print the local Relay version")
    return parser


def _request_from_args(args, config: Config) -> JobRequest:
    return JobRequest(
        task=args.task or "",
        title=args.title,
        task_file=args.task_file,
        worker=args.worker,
        fallback=args.fallback,
        fallback_agents=args.fallback_agents,
        result_format=args.result_format or str(config.get("default_format", "json")),
        output_path=args.output_path,
        artifact_path=args.artifact_path,
        profile=args.profile or str(config.get("default_profile", "web-research")),
        timeout_seconds=args.timeout_seconds,
        caller=args.caller,
        request_id=args.request_id,
        attachments=args.attachments,
        workspace=args.workspace,
        target_path=args.target_path,
        overwrite=args.overwrite,
        machine=args.machine,
        force_new=args.force_new,
        model=args.model,
    )


def _schedule_rule_from_args(args) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "type": args.rule_type,
        "timezone": args.timezone,
    }
    if args.rule_type != "once":
        rule["times"] = list(args.times)
    if args.rule_type == "weekly":
        rule["weekdays"] = list(args.weekdays)
    elif args.rule_type == "monthly":
        rule["month_days"] = list(args.month_days)
        rule["missing_day_policy"] = args.missing_day_policy
    elif args.rule_type == "n_days":
        rule["interval_days"] = args.interval_days
        rule["anchor_date"] = args.anchor_date
    elif args.rule_type == "once":
        rule["run_at_local"] = args.run_at_local
    return rule


def _schedule_create_payload(args) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": args.name,
        "rule": _schedule_rule_from_args(args),
        "overlap_policy": args.overlap_policy,
        "missed_policy": args.missed_policy,
        "missed_grace_seconds": args.missed_grace_seconds,
    }
    for key in ("starts_at_utc", "ends_at_utc", "output_root"):
        value = getattr(args, key, None)
        if value is not None:
            payload[key] = value
    if args.retention_mode:
        retention: dict[str, Any] = {"mode": args.retention_mode}
        if args.retention_value is not None:
            retention["value"] = args.retention_value
        payload["retention"] = retention
    return payload


def _schedule_cli_request(args, config: Config) -> Any:
    client = _ensure_daemon(config)
    command = args.schedule_command
    if command == "create":
        return client.request("POST", f"/v1/schedules/from-job/{args.source_job_id}", _schedule_create_payload(args))
    if command == "preview":
        return client.request(
            "POST",
            "/v1/schedules/preview",
            {
                "rule": _schedule_rule_from_args(args),
                "limit": args.limit,
                "after_utc": args.after_utc,
            },
        )
    if command == "list":
        return client.request("GET", "/v1/schedules")
    if command == "show":
        return client.request("GET", f"/v1/schedules/{args.schedule_id}")
    if command == "runs":
        return client.request("GET", f"/v1/schedules/{args.schedule_id}/runs")
    if command in {"pause", "resume", "run-now"}:
        return client.request("POST", f"/v1/schedules/{args.schedule_id}/{command}")
    if command == "delete":
        return client.request("DELETE", f"/v1/schedules/{args.schedule_id}")
    raise RelayError("INVALID_REQUEST", f"Unknown schedule command: {command}")


_DEFAULT_AGENT_COMMAND_TEMPLATE = "{cli} exec --prompt {request_file} --output {result_file}"


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    sys.stdout.write(f"{prompt}{suffix}: ")
    sys.stdout.flush()
    value = sys.stdin.readline().strip()
    if not value and default is not None:
        return default
    return value


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    sys.stdout.write(f"{prompt} {suffix}: ")
    sys.stdout.flush()
    raw = sys.stdin.readline().strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _apply_agent_registration(config: Config, *, worker_id: str, fields: dict[str, Any]) -> None:
    validate_worker_id(worker_id)
    workers = config.data.setdefault("workers", {})
    if worker_id in workers:
        raise RelayError(
            "AGENT_DUPLICATE",
            f"Worker '{worker_id}' is already registered. Use a different ID.",
        )
    template = fields.get("command_template") or _DEFAULT_AGENT_COMMAND_TEMPLATE
    validate_command_template(template)
    if not fields.get("command"):
        raise RelayError(
            "INVALID_REQUEST",
            "Agent registration requires a non-empty 'command' field.",
        )
    block = dict(fields)
    block["command_template"] = template
    block["enabled"] = bool(fields.get("enabled", True))
    block["require_deep_doctor"] = bool(fields.get("require_deep_doctor", True))
    block["source"] = "user-registered"
    block["registered_at"] = utc_now()
    workers[worker_id] = block
    config.save()


def _run_health_check(
    worker_id: str,
    worker_config: dict[str, Any],
    config: Config,
    db: Database,
    *,
    deep: bool = True,
) -> dict[str, Any]:
    try:
        audit = Doctor(config, db).audit([worker_id], deep=deep)
    except RelayError as err:
        return {
            "shallow_ok": False,
            "deep_ok": False,
            "status": "unhealthy",
            "version": None,
            "error": f"{err.code}: {err.message}",
        }
    workers = audit.get("workers") or []
    spec = workers[0] if workers else {}
    status = spec.get("status", "unhealthy")
    error: str | None = None
    if status != "healthy":
        details = spec.get("details") or {}
        error = (
            details.get("probe_error")
            or f"executable not found: {spec.get('executable') or worker_config.get('command')}"
            if status == "unavailable"
            else f"audit status is {status}"
        )
    return {
        "shallow_ok": bool(spec.get("shallow_ok")),
        "deep_ok": bool(spec.get("deep_ok")),
        "status": status,
        "version": spec.get("version"),
        "error": error,
    }


def _resolve_agent_value(
    args,
    key: str,
    *,
    default: str = "",
    env: str | None = None,
    prompt_fn=_ask,
) -> str:
    raw = getattr(args, key, None)
    if raw:
        return raw
    if env and os.environ.get(env):
        return os.environ[env]
    if not getattr(args, "yes", False):
        return prompt_fn(key.replace("_", " ").capitalize() + ":", default=default or None)
    return default


def _run_add_agent_wizard(
    *,
    prompt_fn=_ask,
    yes_no_fn=_ask_yes_no,
) -> dict[str, Any]:
    print("Relay will register a new external AI CLI as a worker.")
    print("Known placeholders: {cli}, {request_file}, {result_file}, {artifact_dir}, {model}")
    worker_id = prompt_fn("Worker ID (lowercase, e.g. 'opencode')", default=None)
    display_name = prompt_fn("Display name", default=worker_id.capitalize())
    command = prompt_fn("Executable path or name on PATH", default=worker_id)
    template = prompt_fn(
        "Command template (placeholders substituted per job)",
        default=_DEFAULT_AGENT_COMMAND_TEMPLATE,
    )
    default_model = prompt_fn("Default model (blank for none)", default="")
    require_deep = yes_no_fn("Require deep doctor before enabling?", default=True)
    enable = yes_no_fn("Enable this worker after registration?", default=True)
    if not yes_no_fn("Run health check now?", default=True):
        return {
            "worker_id": worker_id,
            "fields": {
                "display_name": display_name,
                "command": command,
                "command_template": template,
                "default_model": default_model,
                "require_deep_doctor": require_deep,
                "enabled": enable,
            },
            "skip_health_check": True,
        }
    return {
        "worker_id": worker_id,
        "fields": {
            "display_name": display_name,
            "command": command,
            "command_template": template,
            "default_model": default_model,
            "require_deep_doctor": require_deep,
            "enabled": enable,
        },
        "skip_health_check": False,
    }


def _collect_agent_fields_noninteractive(args) -> dict[str, Any]:
    worker_id = getattr(args, "worker_id", None) or os.environ.get("RELAY_ADD_AGENT_ID", "")
    if not worker_id:
        raise RelayError(
            "INVALID_REQUEST",
            "Non-interactive mode requires --worker_id or RELAY_ADD_AGENT_ID.",
        )
    display_name = os.environ.get("RELAY_ADD_AGENT_DISPLAY_NAME") or worker_id.capitalize()
    command = os.environ.get("RELAY_ADD_AGENT_COMMAND") or worker_id
    template = os.environ.get("RELAY_ADD_AGENT_COMMAND_TEMPLATE") or _DEFAULT_AGENT_COMMAND_TEMPLATE
    default_model = os.environ.get("RELAY_ADD_AGENT_DEFAULT_MODEL", "")
    require_deep = _parse_bool(os.environ.get("RELAY_ADD_AGENT_REQUIRE_DEEP", "true"), default=True)
    enable = _parse_bool(os.environ.get("RELAY_ADD_AGENT_ENABLE", "true"), default=True)
    description = os.environ.get("RELAY_ADD_AGENT_DESCRIPTION", "")
    extra_args = os.environ.get("RELAY_ADD_AGENT_EXTRA_ARGS", "")
    max_turns_raw = os.environ.get("RELAY_ADD_AGENT_MAX_TURNS", "")
    timeout_raw = os.environ.get("RELAY_ADD_AGENT_TIMEOUT_SECONDS", "")
    fields: dict[str, Any] = {
        "display_name": display_name,
        "command": command,
        "command_template": template,
        "default_model": default_model,
        "require_deep_doctor": require_deep,
        "enabled": enable,
    }
    if description:
        fields["description"] = description
    if extra_args.strip():
        fields["extra_args"] = [token for token in extra_args.split() if token]
    if max_turns_raw:
        try:
            fields["max_turns"] = int(max_turns_raw)
        except ValueError:
            pass
    if timeout_raw:
        try:
            fields["timeout_seconds"] = int(timeout_raw)
        except ValueError:
            pass
    return {
        "worker_id": worker_id,
        "fields": fields,
        "skip_health_check": bool(getattr(args, "skip_health_check", False)),
    }


def _parse_bool(text: str, *, default: bool = False) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return default
    return lowered in {"1", "true", "yes", "y", "on"}


def _run_add_agent(args, config: Config, db: Database) -> dict[str, Any]:
    if getattr(args, "yes", False):
        collected = _collect_agent_fields_noninteractive(args)
    else:
        try:
            is_tty = sys.stdin.isatty()
        except Exception:
            is_tty = False
        if not is_tty:
            raise RelayError(
                "AGENT_NOT_TTY",
                "Interactive 'relay add-agent' requires a TTY. Re-run with --yes or set RELAY_ADD_AGENT_* environment variables.",
            )
        collected = _run_add_agent_wizard()

    worker_id = collected["worker_id"]
    fields = collected["fields"]
    skip_health = collected.get("skip_health_check", False) or bool(getattr(args, "skip_health_check", False))

    if not skip_health:
        health_worker_config = {**fields, "command": fields.get("command") or worker_id}
        health = _run_health_check(worker_id, health_worker_config, config, db, deep=True)
        if health["status"] != "healthy":
            raise RelayError(
                "AGENT_HEALTH_FAILED",
                f"Health check failed for '{worker_id}': {health.get('error') or health.get('status')}. "
                f"Registration aborted; no configuration was saved. Fix the worker and retry, "
                f"or pass --skip-health-check to persist the registration anyway.",
            )

    _apply_agent_registration(config, worker_id=worker_id, fields=fields)
    return {
        "ok": True,
        "status": "registered",
        "worker_id": worker_id,
        "enabled": bool(fields.get("enabled", True)),
        "skipped_health_check": skip_health,
        "config_path": str(config.path),
    }


def _print_json(value: Any, *, compact: bool) -> None:
    text = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
        default=str,
    )
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding:
        text = text.encode(encoding, errors="backslashreplace").decode(encoding)
    print(text)


def _emit(value: Any, machine: bool = False) -> None:
    if machine:
        _print_json(value, compact=True)
        return
    if isinstance(value, dict):
        if value.get("ok") and value.get("status") in {"completed", "partial"}:
            print(f"Status: {value.get('status')}")
            print(f"Job: {value.get('job_id')}")
            if value.get("worker"):
                print(f"Worker: {value.get('worker')}")
            if value.get("result_path"):
                print(f"Result: {value.get('result_path')}")
            if value.get("artifact_path"):
                print(f"Artifacts: {value.get('artifact_path')}")
            if value.get("uncertainties_count"):
                print(f"Uncertainties: {value.get('uncertainties_count')}")
            return
        if value.get("status") in {"queued", "running", "created", "reused"}:
            print(f"Status: {value.get('status')}")
            print(f"Job: {value.get('job_id')}")
            return
    _print_json(value, compact=False)


def _receipt_exit_code(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    if value.get("ok") is False or str(value.get("status", "")).lower() in {"failed", "cancelled"}:
        return 2
    return 0


def _parse_config_value(text: str) -> Any:
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if "," in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _start_daemon(config: Config) -> dict:
    client = RPCClient(config)
    if client.health():
        return {"ok": True, "status": "already_running"}
    runtime = config.path_value("runtime_root")
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / "daemon.log"
    command = entrypoint_command(["daemon", "serve"])
    log_handle = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(config.home),
        "env": {**os.environ, "RELAY_HOME": str(config.home)},
    }
    if command[1:3] == ["-m", "relay"]:
        source_root = Path(__file__).resolve().parent.parent
        if (source_root / "relay").is_dir():
            inherited = kwargs["env"].get("PYTHONPATH", "")
            paths = [str(source_root), *([inherited] if inherited else [])]
            kwargs["env"]["PYTHONPATH"] = os.pathsep.join(paths)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    finally:
        log_handle.close()
    client = RPCClient(config)
    if not client.wait_until_healthy(12):
        raise RelayError("DAEMON_UNAVAILABLE", f"Daemon did not start. See {log_path}")
    return {"ok": True, "status": "started", "log_path": str(log_path)}


def _ensure_daemon(config: Config) -> RPCClient:
    client = RPCClient(config)
    if client.health():
        return client
    if not config.get("daemon_auto_start", True):
        raise RelayError("DAEMON_UNAVAILABLE", "Daemon is not running")
    _start_daemon(config)
    return RPCClient(config)


def _logs(engine: RelayEngine, job_id: str) -> dict:
    job = engine.db.get_job(job_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    attempts = engine.db.attempts_for_job(job_id)
    result = []
    for attempt in attempts:
        item = {
            k: attempt.get(k) for k in ("attempt_id", "worker", "status", "failure_code", "stdout_path", "stderr_path")
        }
        for key in ("stdout_path", "stderr_path"):
            path = attempt.get(key)
            if path and Path(path).exists():
                text = Path(path).read_text(encoding="utf-8", errors="replace")
                item[key.replace("_path", "_tail")] = text[-8000:]
        result.append(item)
    return {"ok": True, "job_id": job_id, "attempts": result}


def main(argv: list[str] | None = None) -> int:
    argv = _preprocess(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config(Path(args.home) if getattr(args, "home", None) else None)
    config.init()
    if args.gui:
        try:
            from .gui.app import run_gui

            return run_gui(config)
        except ImportError:
            print(
                'GUI support is not installed. Run: pip install "relay-ai-cli-broker[gui]"',
                file=sys.stderr,
            )
            return 2
    machine = bool(getattr(args, "machine", False))
    try:
        db = Database(config.path_value("database_path"))
        engine = RelayEngine(config, db)
    except RelayError as err:
        _emit(
            {
                "ok": False,
                "status": "failed",
                "error_code": err.code,
                "error_message": err.message,
                "details": err.details,
            },
            machine,
        )
        return 2
    try:
        # Sync-only users still receive automatic retention cleanup when new work arrives.
        if args.command in {"run", "submit"}:
            cleanup_manager = CleanupManager(config, db)
            if cleanup_manager.due():
                cleanup_manager.run()
        if args.command in (None, "version"):
            _emit({"ok": True, "version": __version__}, machine)
        elif args.command == "init":
            _emit({"ok": True, "config": str(config.init(force=args.force)), "home": str(config.home)}, machine)
        elif args.command == "run":
            result = engine.run(_request_from_args(args, config), submitted_via="cli")
            _emit(result, machine)
            return _receipt_exit_code(result)
        elif args.command == "submit":
            client = _ensure_daemon(config)
            request = _request_from_args(args, config)
            _emit(client.request("POST", "/submit", request.to_dict()), machine)
        elif args.command in {"status", "result", "show"}:
            try:
                client = RPCClient(config)
                value = (
                    client.request("GET", f"/{args.command}/{args.job_id}")
                    if client.health()
                    else (engine.show(args.job_id) if args.command == "show" else engine.receipt(args.job_id))
                )
            except RelayError:
                value = engine.show(args.job_id) if args.command == "show" else engine.receipt(args.job_id)
            _emit(value, machine)
            if args.command in {"status", "result"}:
                return _receipt_exit_code(value)
        elif args.command == "wait":
            deadline = time.monotonic() + args.timeout if args.timeout else None
            while True:
                value = engine.receipt(args.job_id)
                if value.get("status") in {"completed", "partial", "failed", "cancelled"}:
                    _emit(value, machine)
                    return _receipt_exit_code(value)
                if deadline and time.monotonic() >= deadline:
                    raise RelayError("TIMEOUT", "Wait command timed out", True)
                time.sleep(args.interval)
        elif args.command == "cancel":
            client = _ensure_daemon(config)
            _emit(client.request("POST", f"/cancel/{args.job_id}"), machine)
        elif args.command == "logs":
            _emit(_logs(engine, args.job_id), machine)
        elif args.command == "history":
            _emit({"ok": True, "jobs": db.list_jobs(args.status, args.limit)}, machine)
        elif args.command == "rerun":
            _emit(engine.rerun(args.job_id), machine)
        elif args.command == "doctor":
            workers = (
                [args.worker] if args.worker else [item["agent_id"] for item in engine.agent_registry.list_agents()]
            )
            _emit(Doctor(config, db).audit(workers, deep=args.deep), machine)
        elif args.command == "config":
            if args.config_command == "show":
                _emit({"ok": True, "config_path": str(config.path), "config": config.data}, machine)
            elif args.config_command == "set":
                config.set(args.key, _parse_config_value(args.value))
                _emit({"ok": True, "key": args.key, "value": config.get(args.key)}, machine)
            elif args.config_command in {"enable-worker", "disable-worker"}:
                enable = args.config_command == "enable-worker"
                if enable:
                    adapter = __import__("relay.adapters", fromlist=["get_adapter"]).get_adapter(
                        args.worker, config.worker(args.worker), config.path_value("adapter_spec_root")
                    )
                    adapter.require_verified()
                    if args.worker == "antigravity" and not config.get("workers.antigravity.security_verified", False):
                        raise RelayError(
                            "PERMISSION_BLOCKED",
                            "Antigravity activation requires OS isolation review. "
                            "After verifying the dedicated account and ACLs, run: "
                            "relay config set workers.antigravity.security_verified true",
                        )
                config.set(f"workers.{args.worker}.enabled", enable)
                _emit({"ok": True, "worker": args.worker, "enabled": enable}, machine)
        elif args.command == "cleanup":
            manager = CleanupManager(config, db)
            if args.status:
                _emit({"ok": True, **manager.status()}, machine)
            else:
                _emit(manager.run(override_days=args.days, dry_run=args.dry_run), machine)
        elif args.command == "schedule":
            _emit(_schedule_cli_request(args, config), machine)
        elif args.command == "daemon":
            if args.daemon_command == "serve":
                RelayDaemon(config).serve()
                return 0
            if args.daemon_command == "start":
                _emit(_start_daemon(config), machine)
            elif args.daemon_command == "status":
                client = RPCClient(config)
                _emit(
                    client.request("GET", "/health") if client.health() else {"ok": False, "status": "stopped"}, machine
                )
            elif args.daemon_command == "stop":
                client = RPCClient(config)
                _emit(
                    client.request("POST", "/shutdown")
                    if client.health()
                    else {"ok": True, "status": "already_stopped"},
                    machine,
                )
        elif args.command == "security":
            action_worker = args.enable_full_access or args.disable_full_access
            if action_worker:
                enabled = bool(args.enable_full_access)
                client = RPCClient(config)
                if client.health():
                    value = client.request(
                        "PATCH",
                        f"/v1/security/full-access/{action_worker}",
                        {"enabled": enabled},
                    )
                    value["source"] = "daemon"
                else:
                    value = {
                        "ok": True,
                        "full_access_mode": set_full_access_mode(config, action_worker, enabled),
                        "source": "config",
                    }
                _emit(
                    {
                        **value,
                        "worker": action_worker,
                        "enabled": value.get("full_access_mode", {}).get(action_worker, enabled),
                    },
                    machine,
                )
            else:
                client = RPCClient(config)
                value = (
                    client.request("GET", "/v1/security")
                    if client.health()
                    else {"ok": True, **security_posture(config)}
                )
                if args.worker:
                    value["selected_worker"] = args.worker
                    value["selected_enabled"] = bool(value.get("full_access_mode", {}).get(args.worker, False))
                _emit(value, machine)
        elif args.command == "models":
            from .model_discovery import get_model_catalog

            workers = (
                [item["agent_id"] for item in engine.agent_registry.list_agents()]
                if args.worker == "all"
                else [args.worker]
            )
            results = []

            for w in workers:
                try:
                    adapter = engine.agent_registry.get_adapter(w)
                    catalog = get_model_catalog(
                        config, adapter, refresh=args.refresh, include_hidden=args.include_hidden, verify=args.verify
                    )
                    results.append(catalog.to_dict())
                except RelayError as err:
                    results.append(
                        {
                            "worker": w,
                            "status": "error",
                            "error_code": err.code,
                            "error_message": err.message,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "worker": w,
                            "status": "error",
                            "error_message": str(exc),
                        }
                    )

            from .util import utc_now

            response = {
                "schema_version": "1.0",
                "generated_at": utc_now(),
                "workers": results,
            }
            if not machine:
                for w_data in results:
                    print(f"\n{w_data.get('worker', 'Unknown').capitalize()}")
                    if w_data.get("status") == "error":
                        print(f"  Error: {w_data.get('error_message')}")
                        continue
                    for m in w_data.get("models", []):
                        tags = []
                        if m.get("availability"):
                            tags.append(m["availability"])
                        if m.get("is_default"):
                            tags.append("default")
                        tag_str = ", ".join(tags)
                        print(f"  {m.get('id'):<30} {tag_str}")
                    for w in w_data.get("warnings", []):
                        print(f"  Warning: {w}")
            else:
                _emit(response, machine=True)

        elif args.command == "model-check":
            from .model_discovery import probe_claude_model

            # for codex and antigravity, we check catalog. For claude we actually probe if requested.
            adapter = engine.agent_registry.get_adapter(args.worker)
            exe = adapter.executable()
            if not exe:
                raise RelayError("WORKER_NOT_INSTALLED", f"{args.worker} executable not found")

            is_ok = False
            if args.worker == "claude":
                is_ok = probe_claude_model(exe, args.model)
            else:
                # for others, check catalog
                from .model_discovery import get_model_catalog

                cat = get_model_catalog(config, adapter, refresh=False)
                is_ok = any(m.id == args.model or m.selectable_name == args.model for m in cat.models)

            _emit({"ok": is_ok, "worker": args.worker, "model": args.model}, machine)
        elif args.command == "add-agent":
            result = _run_add_agent(args, config, db)
            _emit(result, machine)
        elif args.command == "agent-app":
            from .agent_apps import AgentAppService

            service = AgentAppService(config, db, engine)
            if args.agent_app_command == "list":
                _emit({"ok": True, "agent_apps": service.list()}, machine)
            elif args.agent_app_command == "show":
                _emit({"ok": True, "agent": service.show(args.agent_id)}, machine)
            elif args.agent_app_command == "test":
                _emit({"ok": True, **service.test(args.agent_id)}, machine)
            elif args.agent_app_command in {"enable", "disable"}:
                _emit(
                    {"ok": True, "agent": service.set_enabled(args.agent_id, args.agent_app_command == "enable")},
                    machine,
                )
            elif args.agent_app_command == "delete":
                _emit({"ok": True, "deleted": service.delete(args.agent_id)}, machine)
        return 0
    except RelayError as err:
        _emit(
            {
                "ok": False,
                "status": "failed",
                "error_code": err.code,
                "error_message": err.message,
                "details": err.details,
            },
            machine,
        )
        return 2
    except KeyboardInterrupt:
        _emit({"ok": False, "status": "failed", "error_code": "CANCELLED", "error_message": "Interrupted"}, machine)
        return 130
    except Exception as exc:
        _emit({"ok": False, "status": "failed", "error_code": "INTERNAL_ERROR", "error_message": str(exc)}, machine)
        return 1
