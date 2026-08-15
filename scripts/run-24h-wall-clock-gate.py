#!/usr/bin/env python3
"""Prepare, run, inspect, and finalize the isolated 24-hour wall-clock gate.

The gate never runs from the formal ``.onemancompany`` data root. ``prepare``
creates a consistent read-only-source SQLite backup, hashes protected state,
creates a fresh isolated runtime and standard-v2 iteration, and persists the
fault schedule. ``run`` is a resumable foreground supervisor. It owns only the
isolated backend and injects four bounded faults. Use ``nohup`` when starting a
real 24-hour run so the supervisor survives the invoking terminal.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from onemancompany.core.runtime_storage import RuntimeStorage
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree

PROJECT_ID_PREFIX = "wall-clock-drill"
ITERATION_ID = "iter_001"
PROTECTED_PROJECT_ID = "18b1e9d4a1fc"
MINIMUM_FORMAL_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_PORT = 8015
DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
DEFAULT_FAULT_DURATION_SECONDS = 15.0
REQUIRED_FAULTS = (
    "provider_429",
    "embedding_unavailable",
    "backend_restart",
    "sqlite_lock",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_event(run_root: Path, event: str, **fields: Any) -> None:
    path = run_root / "evidence/events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": _utc_now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return False
    except ChildProcessError:
        # The process may have been adopted from an earlier supervisor.
        pass
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    return bool(status) and not status.startswith("Z")


def _choose_port(requested: int) -> int:
    if requested:
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _protected_paths(formal_root: Path) -> dict[str, Path]:
    iteration_root = formal_root / "company/business/projects" / PROTECTED_PROJECT_ID / "iterations"
    return {
        "runtime_sqlite": formal_root / "data/runtime.sqlite3",
        "legacy_iter_009": iteration_root / "iter_009.yaml",
        "directory_iter_009": iteration_root / "iter_009/task_tree.yaml",
    }


def _hash_paths(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"required baseline file missing: {path}")
        result[name] = {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return result


def _configuration_paths(repo_root: Path, formal_root: Path) -> list[Path]:
    paths = [
        repo_root / "config.yaml",
        repo_root / ".env.example",
        repo_root / "docs/24h-work-mode/team-configuration.md",
        formal_root / "config.yaml",
    ]
    employee_root = formal_root / "company/human_resource/employees"
    for pattern in ("*/profile.yaml", "*/automations.yaml", "*/work_principles.md"):
        paths.extend(sorted(employee_root.glob(pattern)))
    return sorted({path.resolve() for path in paths if path.is_file()})


def _configuration_hashes(repo_root: Path, formal_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _configuration_paths(repo_root, formal_root):
        if _is_inside(path, formal_root):
            label = f"formal:{path.relative_to(formal_root.resolve()).as_posix()}"
        else:
            label = f"repo:{path.relative_to(repo_root.resolve()).as_posix()}"
        records.append({"path": label, "size_bytes": path.stat().st_size, "sha256": _sha256(path)})
    return records


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _sqlite_snapshot(database: Path, *, require_runtime_schema: bool = True) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro",
        uri=True,
        timeout=1.0,
    )
    connection.execute("PRAGMA query_only=ON")
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity_check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        required = {"checkpoints", "store", "memory_outbox", "audit_events"}
        missing = sorted(required - tables)
        if missing and require_runtime_schema:
            raise RuntimeError("runtime database missing required tables: " + ",".join(missing))
        outbox: dict[str, int] = {}
        if "memory_outbox" in tables:
            outbox.update(
                {
                    str(status): int(count)
                    for status, count in connection.execute(
                        "SELECT status,COUNT(*) FROM memory_outbox GROUP BY status ORDER BY status"
                    )
                }
            )
            attempted = connection.execute(
                "SELECT COUNT(*) FROM memory_outbox WHERE attempt != 0"
            ).fetchone()
            outbox["attempted"] = int(attempted[0]) if attempted else 0
        counts = {
            table: _table_count(connection, table)
            for table in (
                "checkpoints",
                "store",
                "memory_outbox",
                "audit_events",
                "dispatch_intents",
                "tool_invocation_ledger",
            )
        }
        return {
            "available": True,
            "quick_check": quick_check,
            "integrity_check": integrity_check,
            "tables": sorted(tables),
            "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
            "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
            "outbox": outbox,
            "counts": counts,
        }
    finally:
        connection.close()


def _safe_sqlite_snapshot(database: Path) -> dict[str, Any]:
    try:
        return _sqlite_snapshot(database)
    except Exception as exc:  # monitoring must survive a bounded lock/outage
        return {"available": False, "error_type": type(exc).__name__, "error": str(exc)[:500]}


def _online_backup(source: Path, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=30)
    source_connection.execute("PRAGMA query_only=ON")
    destination = sqlite3.connect(target)
    try:
        source_connection.backup(destination)
    finally:
        destination.close()
        source_connection.close()
    verification = _sqlite_snapshot(target)
    if verification["quick_check"] != "ok" or verification["integrity_check"] != "ok":
        raise RuntimeError("online backup integrity verification failed")
    return {
        "path": str(target.resolve()),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "backup_method": "sqlite_online_backup_api_read_only_source",
        **verification,
    }


async def _initialize_runtime(database: Path) -> None:
    storage = RuntimeStorage(database)
    await storage.initialize()
    await storage.close()


def _copy_formal_data(formal_root: Path, isolated_root: Path) -> None:
    company_source = formal_root / "company"
    if not company_source.is_dir():
        raise FileNotFoundError(f"formal company directory missing: {company_source}")
    shutil.copytree(company_source, isolated_root / "company", copy_function=shutil.copy2)
    if (formal_root / "config.yaml").is_file():
        shutil.copy2(formal_root / "config.yaml", isolated_root / "config.yaml")
    database_target = isolated_root / "data/runtime.sqlite3"
    database_target.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_initialize_runtime(database_target))
    (isolated_root / "logs").mkdir(parents=True, exist_ok=True)


def _create_iteration(isolated_root: Path, date_token: str) -> dict[str, Any]:
    project_id = f"{PROJECT_ID_PREFIX}-{date_token}"
    qualified_project_id = f"{project_id}/{ITERATION_ID}"
    iteration_dir = (
        isolated_root
        / "company/business/projects"
        / project_id
        / "iterations"
        / ITERATION_ID
    )
    tree_path = iteration_dir / "task_tree.yaml"
    tree = TaskTree(qualified_project_id, mode="standard", workflow_contract_version=2)
    root = tree.create_root(
        "00001",
        "24-hour wall-clock resilience drill. Formal business data is read-only; all execution is isolated.",
    )
    root.title = "24-hour wall-clock resilience drill"
    root.node_type = NodeType.CEO_PROMPT.value
    root.status = TaskPhase.HOLDING.value
    root.hold_reason = "wall_clock_gate_prepared"
    root.hold_started_at = _utc_now()
    root.execution_generation = 1
    root.checkpoint_thread_id = f"omc:{project_id}:{ITERATION_ID}:{root.id}:g1"
    root.checkpoint_status = "waiting"
    root.execution_checkpoint = {
        "phase": "prepared",
        "required_faults": list(REQUIRED_FAULTS),
    }
    root.project_dir = str(iteration_dir.resolve())
    tree.save(tree_path)
    return {
        "project_id": project_id,
        "iteration_id": ITERATION_ID,
        "tree_path": str(tree_path.resolve()),
        "root_node_id": root.id,
        "checkpoint_thread_id": root.checkpoint_thread_id,
        "workflow_contract_version": 2,
        "mode": "standard",
    }


def _fault_schedule(duration_seconds: int) -> dict[str, Any]:
    fractions = (
        ("provider_429", 0.125),
        ("embedding_unavailable", 0.30),
        ("backend_restart", 0.50),
        ("sqlite_lock", 0.75),
    )
    return {
        "schema_version": 1,
        "duration_seconds": duration_seconds,
        "events": [
            {
                "fault": name,
                "offset_seconds": max(1, int(duration_seconds * fraction)),
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "evidence": None,
            }
            for name, fraction in fractions
        ],
    }


def prepare(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.expanduser().resolve()
    formal_root = args.formal_data_root.expanduser().resolve()
    run_root = args.run_root.expanduser().resolve()
    duration_seconds = int(args.duration_seconds)
    port = _choose_port(int(args.port))

    if duration_seconds < MINIMUM_FORMAL_DURATION_SECONDS and not args.test_mode:
        raise ValueError("formal wall-clock gate duration must be at least 86400 seconds")
    if _is_inside(run_root, formal_root):
        raise ValueError("run root must be outside formal data root")
    if run_root == repo_root or _is_inside(repo_root, run_root):
        raise ValueError("run root cannot contain the repository")
    if run_root.exists() and any(run_root.iterdir()):
        raise FileExistsError(f"run root is not empty: {run_root}")

    protected = _protected_paths(formal_root)
    before = _hash_paths(protected)
    configuration = _configuration_hashes(repo_root, formal_root)

    run_root.mkdir(parents=True, exist_ok=True)
    backup_database = run_root / "baseline/runtime.sqlite3"
    runtime_backup = _online_backup(protected["runtime_sqlite"], backup_database)
    isolated_root = run_root / "isolated-data"
    _copy_formal_data(formal_root, isolated_root)
    date_token = datetime.now().astimezone().strftime("%Y%m%d")
    iteration = _create_iteration(isolated_root, date_token)
    schedule = _fault_schedule(duration_seconds)

    # Secrets are loaded into child-process environments only. They are never
    # copied to this isolated .env or any evidence artifact.
    isolated_env = "\n".join(
        [
            "OMC_MEMORY_ENABLED=true",
            "OMC_AUTOMATION_ENABLED=false",
            "OMC_RESTORE_PERSISTED_TASKS=true",
            "OMC_MEMORY_DATABASE_PATH=data/runtime.sqlite3",
            "HOST=127.0.0.1",
            f"PORT={port}",
            "",
        ]
    )
    env_path = isolated_root / ".env"
    env_path.write_text(isolated_env, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    evidence = run_root / "evidence/events.jsonl"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.touch()
    _append_event(
        run_root,
        "wall_clock_gate_prepared",
        project_id=iteration["project_id"],
        iteration_id=iteration["iteration_id"],
        port=port,
    )

    after = _hash_paths(protected)
    source_stable = before == after
    if not source_stable:
        raise RuntimeError("formal protected baseline changed while preparing wall-clock gate")

    isolated_runtime = _sqlite_snapshot(isolated_root / "data/runtime.sqlite3")
    state = {
        "schema_version": 1,
        "status": "prepared",
        "test_mode": bool(args.test_mode),
        "prepared_at": _utc_now(),
        "started_at": None,
        "started_epoch": None,
        "deadline_at": None,
        "deadline_epoch": None,
        "completed_at": None,
        "duration_seconds": duration_seconds,
        "sample_interval_seconds": int(args.sample_interval_seconds),
        "run_root": str(run_root),
        "repo_root": str(repo_root),
        "formal_data_root": str(formal_root),
        "isolated_data_root": str(isolated_root),
        "port": port,
        "iteration": iteration,
        "faults": {item["fault"]: "pending" for item in schedule["events"]},
        "backend": {"pid": None, "start_count": 0, "restart_count": 0, "last_started_at": None},
        "monitoring_samples": 0,
        "monitoring_failures": 0,
    }
    manifest = {
        "schema_version": 1,
        "status": "prepared",
        "prepared_at": state["prepared_at"],
        "formal_data_root": str(formal_root),
        "formal_data_root_touched": False,
        "source_stable_during_prepare": source_stable,
        "baseline": {
            "protected_before": before,
            "protected_after": after,
            "configuration_hashes": configuration,
            "runtime_backup": runtime_backup,
            "outbox": runtime_backup["outbox"],
            "isolated_runtime": isolated_runtime,
        },
        "isolation": {
            "run_root": str(run_root),
            "data_root": str(isolated_root),
            "secrets_persisted_to_evidence": False,
            "formal_outbox_imported_into_live_runtime": False,
        },
        "iteration": iteration,
    }
    _write_json(run_root / "fault-schedule.json", schedule)
    _write_json(run_root / "state.json", state)
    _write_json(run_root / "manifest.json", manifest)
    print(json.dumps({"status": "prepared", "run_root": str(run_root), "port": port}, ensure_ascii=False))
    return 0


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _secret_values(environment: dict[str, str]) -> list[str]:
    values: list[str] = []
    for key, value in environment.items():
        upper = key.upper()
        if value and ("API_KEY" in upper or "TOKEN" in upper or "PASSWORD" in upper):
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def _sanitize(text: str, secrets: list[str]) -> str:
    result = text
    for secret in secrets:
        result = result.replace(secret, "<redacted>")
    return result


def _tree_snapshot(tree_path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(tree_path.read_text(encoding="utf-8")) or {}
    nodes = payload.get("nodes") or []
    root = nodes[0] if nodes else {}
    return {
        "project_id": payload.get("project_id"),
        "mode": payload.get("mode"),
        "workflow_contract_version": payload.get("workflow_contract_version"),
        "root_node_id": root.get("id"),
        "status": root.get("status"),
        "hold_reason": root.get("hold_reason"),
        "checkpoint_thread_id": root.get("checkpoint_thread_id"),
        "checkpoint_status": root.get("checkpoint_status"),
        "execution_generation": root.get("execution_generation"),
        "node_count": len(nodes),
    }


def _health(port: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            body = response.read(1024 * 1024)
            payload = json.loads(body.decode("utf-8"))
            return {"available": True, "http_status": response.status, "payload": payload}
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__, "error": str(exc)[:500]}


def _process_resources(pid: int | None) -> dict[str, Any]:
    if not _pid_alive(pid):
        return {"available": False}
    completed = subprocess.run(
        ["ps", "-o", "rss=,%cpu=", "-p", str(pid)],
        text=True,
        capture_output=True,
        check=False,
    )
    parts = completed.stdout.strip().split()
    if completed.returncode != 0 or len(parts) < 2:
        return {"available": False}
    return {"available": True, "rss_kib": int(parts[0]), "cpu_percent": float(parts[1])}


def _monitor_sample(run_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    isolated_root = Path(state["isolated_data_root"])
    database = isolated_root / "data/runtime.sqlite3"
    disk = shutil.disk_usage(run_root)
    sample = {
        "sampled_at": _utc_now(),
        "elapsed_seconds": max(0.0, time.time() - float(state["started_epoch"])),
        "backend": {
            "pid": state["backend"].get("pid"),
            "alive": _pid_alive(state["backend"].get("pid")),
            "resources": _process_resources(state["backend"].get("pid")),
        },
        "health": _health(int(state["port"])),
        "runtime_sqlite": _safe_sqlite_snapshot(database),
        "task_tree": _tree_snapshot(Path(state["iteration"]["tree_path"])),
        "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
    }
    _append_event(run_root, "monitoring_sample", sample=sample)
    state["monitoring_samples"] = int(state.get("monitoring_samples", 0)) + 1
    if not sample["backend"]["alive"] or not sample["health"]["available"]:
        state["monitoring_failures"] = int(state.get("monitoring_failures", 0)) + 1
    state["last_sample"] = sample
    _write_json(run_root / "state.json", state)
    return sample


def _backend_environment(state: dict[str, Any], embedding_env_file: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(_load_env_file(embedding_env_file))
    environment.update(
        {
            "OMC_DATA_ROOT": state["isolated_data_root"],
            "OMC_MEMORY_ENABLED": "true",
            "OMC_AUTOMATION_ENABLED": "false",
            "OMC_RESTORE_PERSISTED_TASKS": "true",
            "OMC_MEMORY_DATABASE_PATH": "data/runtime.sqlite3",
            "HOST": "127.0.0.1",
            "PORT": str(state["port"]),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _backend_command(args: argparse.Namespace) -> list[str]:
    if args.backend_command:
        command = shlex.split(args.backend_command)
        if not command:
            raise ValueError("backend command cannot be empty")
        return command
    return [sys.executable, "-m", "onemancompany.main"]


def _start_backend(
    run_root: Path,
    state: dict[str, Any],
    args: argparse.Namespace,
    *,
    restarted: bool = False,
) -> subprocess.Popen[bytes]:
    existing_pid = state["backend"].get("pid")
    if _pid_alive(existing_pid):
        raise RuntimeError(f"isolated backend already alive with pid {existing_pid}")
    if not _port_available(int(state["port"])):
        raise RuntimeError(f"isolated backend port already in use: {state['port']}")
    log_path = run_root / "logs/backend.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = _backend_environment(state, args.embedding_env_file)
    command = _backend_command(args)
    log_handle = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        cwd=state["repo_root"],
        env=environment,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()
    state["backend"]["pid"] = process.pid
    state["backend"]["start_count"] = int(state["backend"].get("start_count", 0)) + 1
    state["backend"]["last_started_at"] = _utc_now()
    if restarted:
        state["backend"]["restart_count"] = int(state["backend"].get("restart_count", 0)) + 1
    _write_json(run_root / "state.json", state)
    _append_event(
        run_root,
        "backend_restarted" if restarted else "backend_started",
        pid=process.pid,
        port=state["port"],
        start_count=state["backend"]["start_count"],
        restart_count=state["backend"]["restart_count"],
    )
    deadline = time.time() + float(args.backend_ready_timeout_seconds)
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated backend exited during startup with code {process.returncode}")
        if _health(int(state["port"]))["available"]:
            return process
        time.sleep(0.2)
    _terminate_backend(state)
    raise TimeoutError("isolated backend did not become healthy before timeout")


def _terminate_pid(pid: int, timeout_seconds: float = 15.0) -> None:
    if not _pid_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    deadline = time.time() + timeout_seconds
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.1)
    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except PermissionError:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass


def _terminate_backend(state: dict[str, Any]) -> None:
    pid = state.get("backend", {}).get("pid")
    if pid:
        _terminate_pid(int(pid))
    state["backend"]["pid"] = None


def _run_sidecar(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_path: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    secrets = _secret_values(environment)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        _sanitize(completed.stdout + "\n--- stderr ---\n" + completed.stderr, secrets),
        encoding="utf-8",
    )
    return completed


def _fault_provider_429(run_root: Path, state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    report_path = run_root / "evidence/provider-429-report.json"
    if args.test_mode:
        report = {
            "status": "passed",
            "test_mode": True,
            "result": {
                "checks": {
                    "http_429_observed": True,
                    "task_holding_persisted": True,
                    "backoff_persisted": True,
                    "same_checkpoint_thread_resumed": True,
                    "formal_task_priority_preserved": True,
                    "memory_worker_yielded": True,
                    "no_duplicate_side_effects": True,
                    "recovery_ui_visible": True,
                }
            },
        }
        _write_json(report_path, report)
        return {"report": str(report_path), "status": "passed", "scope": "accelerated-test"}
    data_root = run_root / "faults/provider-429/data"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(state["repo_root"]) / "scripts/check-real-provider-429-gate.py"),
        "--data-root",
        str(data_root),
        "--report",
        str(report_path),
        "--keep-data",
    ]
    environment = dict(os.environ)
    completed = _run_sidecar(
        command,
        cwd=Path(state["repo_root"]),
        environment=environment,
        log_path=run_root / "logs/provider-429.log",
        timeout_seconds=int(args.sidecar_timeout_seconds),
    )
    report = _read_json(report_path) if report_path.is_file() else {}
    if completed.returncode != 0 or report.get("status") != "passed":
        raise RuntimeError(f"provider 429 gate failed with code {completed.returncode}")
    return {"report": str(report_path), "status": "passed", "scope": "isolated-provider-sidecar"}


def _fault_embedding(run_root: Path, state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    report_path = run_root / "evidence/embedding-recovery-report.json"
    if args.test_mode:
        report = {
            "status": "passed",
            "test_mode": True,
            "result": {
                "checks": {
                    "real_endpoint_probe": True,
                    "pending_during_outage": True,
                    "durable_backoff": True,
                    "indexed_after_recovery": True,
                    "business_execution_not_failed": True,
                }
            },
        }
        _write_json(report_path, report)
        return {"report": str(report_path), "status": "passed", "scope": "accelerated-test"}
    data_root = run_root / "faults/embedding/data"
    environment = dict(os.environ)
    environment.update(_load_env_file(args.embedding_env_file))
    required = (
        "OMC_MEMORY_EMBEDDING_BASE_URL",
        "OMC_MEMORY_EMBEDDING_API_KEY",
        "OMC_MEMORY_EMBEDDING_MODEL",
        "OMC_MEMORY_EMBEDDING_DIMENSIONS",
        "OMC_MEMORY_INDEX_VERSION",
    )
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise RuntimeError("embedding fault missing configuration: " + ", ".join(missing))
    command = [
        sys.executable,
        str(Path(state["repo_root"]) / "scripts/check-embedding-recovery-gate.py"),
        "--data-root",
        str(data_root),
        "--report",
        str(report_path),
        "--keep-data-root",
    ]
    completed = _run_sidecar(
        command,
        cwd=Path(state["repo_root"]),
        environment=environment,
        log_path=run_root / "logs/embedding-recovery.log",
        timeout_seconds=int(args.sidecar_timeout_seconds),
    )
    report = _read_json(report_path) if report_path.is_file() else {}
    if completed.returncode != 0 or report.get("status") != "passed":
        raise RuntimeError(f"embedding recovery gate failed with code {completed.returncode}")
    return {"report": str(report_path), "status": "passed", "scope": "isolated-embedding-sidecar"}


def _fault_backend_restart(
    run_root: Path,
    state: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], subprocess.Popen[bytes]]:
    database = Path(state["isolated_data_root"]) / "data/runtime.sqlite3"
    before_runtime = _safe_sqlite_snapshot(database)
    before_tree = _tree_snapshot(Path(state["iteration"]["tree_path"]))
    old_pid = state["backend"].get("pid")
    _terminate_backend(state)
    _write_json(run_root / "state.json", state)
    time.sleep(float(args.fault_duration_seconds))
    process = _start_backend(run_root, state, args, restarted=True)
    after_runtime = _safe_sqlite_snapshot(database)
    after_tree = _tree_snapshot(Path(state["iteration"]["tree_path"]))
    before_counts = before_runtime.get("counts", {})
    after_counts = after_runtime.get("counts", {})
    checks = {
        "backend_pid_changed": bool(old_pid) and old_pid != process.pid,
        "health_recovered": _health(int(state["port"]))["available"],
        "same_checkpoint_thread": (
            before_tree.get("checkpoint_thread_id")
            == after_tree.get("checkpoint_thread_id")
            == state["iteration"]["checkpoint_thread_id"]
        ),
        "dispatch_not_duplicated": before_counts.get("dispatch_intents", 0)
        == after_counts.get("dispatch_intents", 0),
        "side_effect_not_duplicated": before_counts.get("tool_invocation_ledger", 0)
        == after_counts.get("tool_invocation_ledger", 0),
        "sqlite_integrity_ok": after_runtime.get("integrity_check") == "ok",
    }
    if not all(checks.values()):
        raise RuntimeError("backend restart checks failed: " + ", ".join(k for k, v in checks.items() if not v))
    report_path = run_root / "evidence/backend-restart-report.json"
    _write_json(
        report_path,
        {
            "status": "passed",
            "old_pid": old_pid,
            "new_pid": process.pid,
            "checks": checks,
            "before_runtime": before_runtime,
            "after_runtime": after_runtime,
            "before_tree": before_tree,
            "after_tree": after_tree,
        },
    )
    return {"report": str(report_path), "status": "passed", "scope": "isolated-live-backend"}, process


def _fault_sqlite_lock(run_root: Path, state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    database = Path(state["isolated_data_root"]) / "data/runtime.sqlite3"
    holder = sqlite3.connect(database, timeout=1.0, isolation_level=None)
    lock_observed = False
    try:
        holder.execute("BEGIN EXCLUSIVE")
        contender = sqlite3.connect(database, timeout=0.05, isolation_level=None)
        try:
            try:
                contender.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                lock_observed = "locked" in str(exc).lower()
        finally:
            contender.close()
        _append_event(
            run_root,
            "sqlite_lock_active",
            database=str(database),
            duration_seconds=float(args.fault_duration_seconds),
            competing_write_blocked=lock_observed,
        )
        time.sleep(float(args.fault_duration_seconds))
    finally:
        try:
            holder.rollback()
        finally:
            holder.close()
    after = _sqlite_snapshot(database)
    checks = {
        "competing_write_blocked": lock_observed,
        "backend_survived": _pid_alive(state["backend"].get("pid")),
        "health_recovered": _health(int(state["port"]))["available"],
        "sqlite_integrity_ok": after.get("integrity_check") == "ok",
    }
    if not all(checks.values()):
        raise RuntimeError("sqlite lock checks failed: " + ", ".join(k for k, v in checks.items() if not v))
    report_path = run_root / "evidence/sqlite-lock-report.json"
    _write_json(report_path, {"status": "passed", "checks": checks, "after": after})
    return {"report": str(report_path), "status": "passed", "scope": "isolated-live-runtime"}


def _execute_fault(
    fault: str,
    run_root: Path,
    state: dict[str, Any],
    args: argparse.Namespace,
    backend_process: subprocess.Popen[bytes] | None,
) -> tuple[dict[str, Any], subprocess.Popen[bytes] | None]:
    if fault == "provider_429":
        return _fault_provider_429(run_root, state, args), backend_process
    if fault == "embedding_unavailable":
        return _fault_embedding(run_root, state, args), backend_process
    if fault == "backend_restart":
        return _fault_backend_restart(run_root, state, args)
    if fault == "sqlite_lock":
        return _fault_sqlite_lock(run_root, state, args), backend_process
    raise ValueError(f"unknown fault: {fault}")


def _acquire_supervisor_lock(run_root: Path) -> Path:
    lock = run_root / "supervisor.lock"
    if lock.exists():
        try:
            existing_pid = int(lock.read_text(encoding="utf-8").strip())
        except ValueError:
            existing_pid = 0
        if _pid_alive(existing_pid):
            raise RuntimeError(f"wall-clock supervisor already running with pid {existing_pid}")
        lock.unlink(missing_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return lock


def _ensure_run_root(run_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state_path = run_root / "state.json"
    manifest_path = run_root / "manifest.json"
    schedule_path = run_root / "fault-schedule.json"
    if not (state_path.is_file() and manifest_path.is_file() and schedule_path.is_file()):
        raise FileNotFoundError("run root is not prepared")
    return _read_json(state_path), _read_json(manifest_path), _read_json(schedule_path)


def _recover_or_start_backend(
    run_root: Path,
    state: dict[str, Any],
    args: argparse.Namespace,
) -> subprocess.Popen[bytes] | None:
    pid = state["backend"].get("pid")
    if _pid_alive(pid) and _health(int(state["port"]))["available"]:
        _append_event(run_root, "backend_adopted", pid=pid, port=state["port"])
        return None
    state["backend"]["pid"] = None
    return _start_backend(run_root, state, args, restarted=bool(state["backend"].get("start_count")))


def _final_checks(
    run_root: Path,
    state: dict[str, Any],
    manifest: dict[str, Any],
    schedule: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    elapsed = max(0.0, time.time() - float(state.get("started_epoch") or time.time()))
    formal_root = Path(state["formal_data_root"])
    formal_after = _hash_paths(_protected_paths(formal_root))
    formal_before = manifest["baseline"]["protected_before"]
    isolated_runtime = _safe_sqlite_snapshot(Path(state["isolated_data_root"]) / "data/runtime.sqlite3")
    tree = _tree_snapshot(Path(state["iteration"]["tree_path"]))
    initial_counts = manifest["baseline"]["isolated_runtime"].get("counts", {})
    final_counts = isolated_runtime.get("counts", {})
    checks = {
        "wall_clock_elapsed": elapsed >= int(state["duration_seconds"]),
        "all_faults_passed": all(item.get("status") == "passed" for item in schedule["events"]),
        "monitoring_active": int(state.get("monitoring_samples", 0)) > 0,
        "backend_restart_observed": int(state["backend"].get("restart_count", 0)) >= 1,
        "same_checkpoint_thread": tree.get("checkpoint_thread_id") == state["iteration"]["checkpoint_thread_id"],
        "tasktree_contract_v2": tree.get("workflow_contract_version") == 2 and tree.get("mode") == "standard",
        "sqlite_integrity_ok": isolated_runtime.get("integrity_check") == "ok",
        "dispatch_not_duplicated": final_counts.get("dispatch_intents", 0)
        == initial_counts.get("dispatch_intents", 0),
        "side_effect_not_duplicated": final_counts.get("tool_invocation_ledger", 0)
        == initial_counts.get("tool_invocation_ledger", 0),
        "formal_protected_hashes_unchanged": formal_before == formal_after,
        "formal_outbox_not_imported_into_live_runtime": bool(
            manifest["isolation"].get("formal_outbox_imported_into_live_runtime") is False
        ),
    }
    details = {
        "elapsed_seconds": elapsed,
        "formal_before": formal_before,
        "formal_after": formal_after,
        "isolated_runtime": isolated_runtime,
        "task_tree": tree,
    }
    return checks, details


def finalize(args: argparse.Namespace) -> int:
    run_root = args.run_root.expanduser().resolve()
    state, manifest, schedule = _ensure_run_root(run_root)
    if state.get("status") == "completed" and (run_root / "final-report.json").is_file():
        report = _read_json(run_root / "final-report.json")
        print(json.dumps({"status": state["status"], "report": str(run_root / "final-report.json")}, ensure_ascii=False))
        return 0 if report.get("status") == "passed" else 1
    if not state.get("started_epoch"):
        raise RuntimeError("wall-clock run has not started")
    checks, details = _final_checks(run_root, state, manifest, schedule)
    failed = [name for name, passed in checks.items() if not passed]
    report = {
        "schema_version": 1,
        "status": "passed" if not failed else "failed",
        "test_mode": bool(state.get("test_mode")),
        "generated_at": _utc_now(),
        "run_root": str(run_root),
        "duration_seconds": state["duration_seconds"],
        "checks": checks,
        "failed_checks": failed,
        "details": details,
        "faults": schedule["events"],
        "formal_24h_launch_allowed": bool(not failed and not state.get("test_mode")),
        "next_stage": (
            "real_device_smoke_and_final_four_person_standard_v2_iteration"
            if not failed and not state.get("test_mode")
            else "wall_clock_gate_not_formally_accepted"
        ),
    }
    _write_json(run_root / "final-report.json", report)
    state["status"] = "completed" if not failed else "failed"
    state["completed_at"] = _utc_now()
    state["final_report"] = str(run_root / "final-report.json")
    manifest["status"] = state["status"]
    manifest["completed_at"] = state["completed_at"]
    _write_json(run_root / "state.json", state)
    _write_json(run_root / "manifest.json", manifest)
    _append_event(run_root, "wall_clock_gate_finalized", status=report["status"], failed_checks=failed)
    print(json.dumps({"status": report["status"], "report": str(run_root / "final-report.json"), "failed_checks": failed}, ensure_ascii=False))
    return 0 if not failed else 1


def run(args: argparse.Namespace) -> int:
    run_root = args.run_root.expanduser().resolve()
    state, manifest, schedule = _ensure_run_root(run_root)
    if state.get("test_mode") and not args.test_mode:
        raise RuntimeError("prepared accelerated run requires --test-mode")
    if not state.get("test_mode") and args.test_mode:
        raise RuntimeError("cannot convert a formal run into test mode")
    if state.get("status") == "completed":
        print(json.dumps({"status": "completed", "run_root": str(run_root)}, ensure_ascii=False))
        return 0
    if state.get("status") == "failed":
        raise RuntimeError("failed run cannot resume without a new prepared run root")

    lock = _acquire_supervisor_lock(run_root)
    backend_process: subprocess.Popen[bytes] | None = None
    try:
        schedule_changed = False
        for item in schedule["events"]:
            if item.get("status") != "running":
                continue
            evidence = item.get("evidence") or {}
            report_path = Path(str(evidence.get("report", ""))) if evidence.get("report") else None
            report = _read_json(report_path) if report_path and report_path.is_file() else {}
            if report.get("status") == "passed":
                item["status"] = "passed"
                state["faults"][item["fault"]] = "passed"
                _append_event(
                    run_root,
                    "fault_completion_reconciled",
                    fault=item["fault"],
                    report=str(report_path),
                )
            else:
                item["status"] = "pending"
                item["started_at"] = None
                item["completed_at"] = None
                item["evidence"] = None
                item.pop("error", None)
                item.pop("error_type", None)
                state["faults"][item["fault"]] = "pending"
                _append_event(
                    run_root,
                    "fault_requeued_after_supervisor_restart",
                    fault=item["fault"],
                )
            schedule_changed = True
        if schedule_changed:
            _write_json(run_root / "fault-schedule.json", schedule)
            _write_json(run_root / "state.json", state)

        now = time.time()
        if not state.get("started_epoch"):
            state["status"] = "running"
            state["started_epoch"] = now
            state["started_at"] = _utc_now()
            state["deadline_epoch"] = now + int(state["duration_seconds"])
            state["deadline_at"] = datetime.fromtimestamp(
                state["deadline_epoch"], timezone.utc
            ).replace(microsecond=0).isoformat()
            manifest["status"] = "running"
            manifest["started_at"] = state["started_at"]
            _write_json(run_root / "manifest.json", manifest)
            _write_json(run_root / "state.json", state)
            _append_event(
                run_root,
                "wall_clock_gate_started",
                started_at=state["started_at"],
                deadline_at=state["deadline_at"],
                duration_seconds=state["duration_seconds"],
            )
        else:
            state["status"] = "running"
            _write_json(run_root / "state.json", state)
            _append_event(run_root, "wall_clock_supervisor_resumed", deadline_at=state["deadline_at"])

        backend_process = _recover_or_start_backend(run_root, state, args)
        while True:
            now = time.time()
            if now >= float(state["deadline_epoch"]):
                break
            pid = state["backend"].get("pid")
            if not _pid_alive(pid):
                backend_process = _start_backend(run_root, state, args, restarted=True)
            for item in schedule["events"]:
                if item.get("status") != "pending":
                    continue
                if now - float(state["started_epoch"]) < float(item["offset_seconds"]):
                    continue
                fault = str(item["fault"])
                item["status"] = "running"
                item["started_at"] = _utc_now()
                state["faults"][fault] = "running"
                _write_json(run_root / "fault-schedule.json", schedule)
                _write_json(run_root / "state.json", state)
                _append_event(run_root, "fault_started", fault=fault, offset_seconds=item["offset_seconds"])
                try:
                    evidence, backend_process = _execute_fault(
                        fault,
                        run_root,
                        state,
                        args,
                        backend_process,
                    )
                except Exception as exc:
                    item["status"] = "failed"
                    item["completed_at"] = _utc_now()
                    item["error_type"] = type(exc).__name__
                    item["error"] = str(exc)[:1000]
                    state["faults"][fault] = "failed"
                    state["status"] = "failed"
                    _write_json(run_root / "fault-schedule.json", schedule)
                    _write_json(run_root / "state.json", state)
                    _append_event(run_root, "fault_failed", fault=fault, error_type=type(exc).__name__, error=str(exc)[:500])
                    raise
                item["status"] = "passed"
                item["completed_at"] = _utc_now()
                item["evidence"] = evidence
                state["faults"][fault] = "passed"
                _write_json(run_root / "fault-schedule.json", schedule)
                _write_json(run_root / "state.json", state)
                _append_event(run_root, "fault_completed", fault=fault, evidence=evidence)
                now = time.time()
            _monitor_sample(run_root, state)
            remaining = float(state["deadline_epoch"]) - time.time()
            if remaining <= 0:
                break
            time.sleep(min(float(state["sample_interval_seconds"]), remaining))

        _monitor_sample(run_root, state)
        _terminate_backend(state)
        _write_json(run_root / "state.json", state)
        return finalize(argparse.Namespace(run_root=run_root))
    except Exception:
        state["status"] = "failed"
        state["failed_at"] = _utc_now()
        _terminate_backend(state)
        _write_json(run_root / "state.json", state)
        raise
    finally:
        lock.unlink(missing_ok=True)


def status(args: argparse.Namespace) -> int:
    run_root = args.run_root.expanduser().resolve()
    state, _, schedule = _ensure_run_root(run_root)
    payload = {
        "status": state.get("status"),
        "run_root": str(run_root),
        "started_at": state.get("started_at"),
        "deadline_at": state.get("deadline_at"),
        "completed_at": state.get("completed_at"),
        "duration_seconds": state.get("duration_seconds"),
        "elapsed_seconds": (
            max(0.0, time.time() - float(state["started_epoch"]))
            if state.get("started_epoch")
            else 0.0
        ),
        "backend_alive": _pid_alive(state.get("backend", {}).get("pid")),
        "monitoring_samples": state.get("monitoring_samples", 0),
        "faults": {item["fault"]: item["status"] for item in schedule["events"]},
        "final_report": state.get("final_report"),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subcommands.add_parser("prepare", help="create an isolated 24-hour run")
    prepare_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    prepare_parser.add_argument("--formal-data-root", type=Path, default=Path.cwd() / ".onemancompany")
    prepare_parser.add_argument("--run-root", type=Path, required=True)
    prepare_parser.add_argument("--duration-seconds", type=int, default=MINIMUM_FORMAL_DURATION_SECONDS)
    prepare_parser.add_argument("--sample-interval-seconds", type=int, default=DEFAULT_SAMPLE_INTERVAL_SECONDS)
    prepare_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="isolated backend port; 0 selects a free port")
    prepare_parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    prepare_parser.set_defaults(handler=prepare)

    run_parser = subcommands.add_parser("run", help="run or resume the wall-clock supervisor")
    run_parser.add_argument("--run-root", type=Path, required=True)
    run_parser.add_argument("--embedding-env-file", type=Path, default=Path.cwd() / ".env.embedding.local")
    run_parser.add_argument("--backend-command", default="", help=argparse.SUPPRESS)
    run_parser.add_argument("--fault-duration-seconds", type=float, default=DEFAULT_FAULT_DURATION_SECONDS)
    run_parser.add_argument("--backend-ready-timeout-seconds", type=float, default=90.0)
    run_parser.add_argument("--sidecar-timeout-seconds", type=int, default=600)
    run_parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    run_parser.set_defaults(handler=run)

    status_parser = subcommands.add_parser("status", help="show durable wall-clock status")
    status_parser.add_argument("--run-root", type=Path, required=True)
    status_parser.set_defaults(handler=status)

    finalize_parser = subcommands.add_parser("finalize", help="verify elapsed time and final evidence")
    finalize_parser.add_argument("--run-root", type=Path, required=True)
    finalize_parser.set_defaults(handler=finalize)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"wall-clock gate blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
