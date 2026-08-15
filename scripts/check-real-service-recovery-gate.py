#!/usr/bin/env python3
"""Run the isolated standard-v2 three-boundary service recovery gate.

The gate never stops the formal service or writes to the repository's formal
``.onemancompany`` data root.  It starts fresh service worker processes, kills
them after durable checkpoints at three business boundaries, resumes each
thread, performs an online SQLite backup, restores it to an independent data
root, and reconciles the restored copy read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from onemancompany.core.runtime_storage import RuntimeStorage
from onemancompany.core.task_tree import TaskTree

PROJECT_ID = "recovery-drill-20260815"
ITERATION_ID = "iter_001"
PARENT_ID = "a00000000001"
SCENARIOS = {
    "dispatch": "b00000000001",
    "executor_started": "b00000000002",
    "side_effect": "b00000000003",
}
EXPECTED_CRASH_CODE = 87


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _formal_snapshot(repo: Path) -> dict[str, Any]:
    formal_root = repo / ".onemancompany"
    database = formal_root / "data" / "runtime.sqlite3"
    protected = {
        "runtime.sqlite3": database,
        "legacy_iter_009": formal_root / "company/business/projects/18b1e9d4a1fc/iterations/iter_009.yaml",
        "directory_iter_009": formal_root / "company/business/projects/18b1e9d4a1fc/iterations/iter_009/task_tree.yaml",
    }
    outbox: dict[str, int] = {}
    if database.exists():
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_outbox'"
            ).fetchone()
            if table:
                outbox = {
                    str(status): int(count)
                    for status, count in connection.execute(
                        "SELECT status,COUNT(*) FROM memory_outbox GROUP BY status ORDER BY status"
                    )
                }
                attempted = connection.execute(
                    "SELECT COUNT(*) FROM memory_outbox WHERE attempt != 0"
                ).fetchone()
                outbox["attempted"] = int(attempted[0]) if attempted else 0
        finally:
            connection.close()
    return {
        "hashes": {name: _sha256(path) for name, path in protected.items()},
        "outbox": outbox,
    }


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _tree_path(root: Path) -> Path:
    return (
        root
        / "company/business/projects"
        / PROJECT_ID
        / "iterations"
        / ITERATION_ID
        / "task_tree.yaml"
    )


def _db_snapshot(database: Path, tree_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity_check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        dispatch = [
            {
                "parent_id": str(row["parent_id"]),
                "employee_id": str(row["employee_id"]),
                "task_key": str(row["task_key"]),
                "node_id": str(row["node_id"]),
                "state": str(row["state"]),
                "receipt": json.loads(str(row["receipt_json"])) if row["receipt_json"] else None,
            }
            for row in connection.execute(
                "SELECT parent_id,employee_id,task_key,node_id,state,receipt_json "
                "FROM dispatch_intents ORDER BY task_key"
            )
        ]
        ledger = [
            {
                "node_id": str(row["node_id"]),
                "generation": int(row["execution_generation"]),
                "tool_name": str(row["tool_name"]),
                "business_key": str(row["business_idempotency_key"]),
                "status": str(row["status"]),
                "result": json.loads(str(row["result_json"])) if row["result_json"] else None,
            }
            for row in connection.execute(
                "SELECT node_id,execution_generation,tool_name,business_idempotency_key,status,result_json "
                "FROM tool_invocation_ledger ORDER BY node_id,tool_name"
            )
        ]
        checkpoints = {
            str(row["thread_id"]): int(row["count"])
            for row in connection.execute(
                "SELECT thread_id,COUNT(*) AS count FROM checkpoints GROUP BY thread_id ORDER BY thread_id"
            )
        }
        outbox = [
            {
                "event_id": str(row["event_id"]),
                "status": str(row["status"]),
                "attempt": int(row["attempt"]),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in connection.execute(
                "SELECT event_id,status,attempt,payload_json FROM memory_outbox ORDER BY event_id"
            )
        ]
        read_only_enforced = False
        try:
            connection.execute("CREATE TABLE should_not_write(value TEXT)")
        except sqlite3.OperationalError:
            read_only_enforced = True
    finally:
        connection.close()

    tree = TaskTree.load(tree_path, skeleton_only=False)
    nodes = {}
    for scenario, node_id in SCENARIOS.items():
        node = tree.get_node(node_id)
        nodes[scenario] = {
            "node_id": node_id,
            "status": node.status if node else None,
            "task_key": node.task_key if node else None,
            "execution_generation": node.execution_generation if node else None,
            "checkpoint_thread_id": node.checkpoint_thread_id if node else None,
            "checkpoint_status": node.checkpoint_status if node else None,
            "dispatch_verification": dict(node.dispatch_verification or {}) if node else {},
            "execution_checkpoint": dict(node.execution_checkpoint or {}) if node else {},
            "acceptance_audit": dict(node.acceptance_audit or {}) if node else {},
        }
    parent = tree.get_node(PARENT_ID)
    return {
        "quick_check": quick_check,
        "integrity_check": integrity_check,
        "read_only_enforced": read_only_enforced,
        "dispatch_intents": dispatch,
        "tool_ledger": ledger,
        "checkpoints": checkpoints,
        "memory_outbox": outbox,
        "tree": {
            "project_id": tree.project_id,
            "mode": tree.mode,
            "workflow_contract_version": tree.workflow_contract_version,
            "root_id": tree.root_id,
            "parent_children": list(parent.children_ids) if parent else [],
            "nodes": nodes,
        },
    }


async def _online_backup(database: Path, backup_dir: Path) -> dict[str, Any]:
    storage = RuntimeStorage(database)
    await storage.initialize()
    try:
        result = await storage.backup(
            backup_dir,
            application_version="service-recovery-gate",
            backup_id="service-recovery-20260815",
        )
        return {
            "database_path": str(result.database_path),
            "manifest_path": str(result.manifest_path),
            "integrity_check_result": result.integrity_check_result,
            "manifest": json.loads(result.manifest_path.read_text(encoding="utf-8")),
        }
    finally:
        await storage.close()


def _run_worker(repo: Path, phase: str, scenario: str, data_root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    worker = repo / "tests/integration/service_recovery_gate_worker.py"
    environment = os.environ.copy()
    source_root = str(repo / "src")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(worker), phase, scenario, str(data_root), str(output)],
        cwd=repo,
        env=environment,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def _contains_secret(path: Path) -> bool:
    if not path.exists():
        return False
    needles = (b"sk-", b"OMC_MEMORY_EMBEDDING_API_KEY", b"OPENAI_API_KEY", b"api_key=")
    content = path.read_bytes()
    return any(needle in content for needle in needles)


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--restore-root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    formal_root = (repo / ".onemancompany").resolve()
    data_root = (args.data_root or Path(tempfile.mkdtemp(prefix="omc-service-recovery-gate-"))).expanduser().resolve()
    restore_root = (
        args.restore_root or data_root.parent / f"{data_root.name}-readonly-restore"
    ).expanduser().resolve()
    report_path = args.report.expanduser().resolve()
    formal_before = _formal_snapshot(repo)

    blocked_reason = None
    if _inside(data_root, formal_root) or _inside(restore_root, formal_root):
        blocked_reason = "recovery gate refuses the formal .onemancompany data root"
    elif data_root == restore_root or _inside(restore_root, data_root) or _inside(data_root, restore_root):
        blocked_reason = "restore root must be independent from the drill data root"
    elif data_root.exists() and any(data_root.iterdir()):
        blocked_reason = "drill data root must be new and empty"
    elif restore_root.exists() and any(restore_root.iterdir()):
        blocked_reason = "restore data root must be new and empty"

    if blocked_reason:
        payload = {
            "status": "blocked",
            "error": blocked_reason,
            "formal_before": formal_before,
            "formal_after": _formal_snapshot(repo),
            "formal_outbox_touched": False,
            "formal_24h_launch_allowed": False,
        }
        payload["formal_outbox_touched"] = payload["formal_before"] != payload["formal_after"]
        _write_report(report_path, payload)
        print(blocked_reason, file=sys.stderr)
        return 2

    data_root.mkdir(parents=True, exist_ok=True)
    evidence_dir = data_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    maintenance = {
        "window_id": "isolated-recovery-drill-20260815",
        "scope": "isolated_temp_data_root",
        "status": "active",
        "started_at": _now(),
        "formal_service_stop_authorized": False,
        "formal_data_writes_authorized": False,
        "drill_data_root": str(data_root),
        "restore_data_root": str(restore_root),
    }
    maintenance_path = evidence_dir / "maintenance-window.json"
    maintenance_path.write_text(json.dumps(maintenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    runs: dict[str, Any] = {}
    try:
        for scenario in SCENARIOS:
            crash_output = evidence_dir / f"{scenario}-crash.json"
            resume_output = evidence_dir / f"{scenario}-resume.json"
            crashed = _run_worker(repo, "crash", scenario, data_root, crash_output)
            crash_payload = json.loads(crash_output.read_text(encoding="utf-8")) if crash_output.exists() else {}
            resumed = _run_worker(repo, "resume", scenario, data_root, resume_output)
            resume_payload = json.loads(resume_output.read_text(encoding="utf-8")) if resume_output.exists() else {}
            runs[scenario] = {
                "crash_returncode": crashed.returncode,
                "crash_stderr": crashed.stderr[-2000:],
                "resume_returncode": resumed.returncode,
                "resume_stderr": resumed.stderr[-2000:],
                "crash": crash_payload,
                "resume": resume_payload,
            }
            if crashed.returncode != EXPECTED_CRASH_CODE or resumed.returncode != 0:
                raise RuntimeError(
                    f"scenario {scenario} failed: crash={crashed.returncode}, resume={resumed.returncode}"
                )

        source_database = data_root / "data/runtime.sqlite3"
        source_tree = _tree_path(data_root)
        source_snapshot = _db_snapshot(source_database, source_tree)
        backup = asyncio.run(_online_backup(source_database, data_root / "backups"))

        restore_root.mkdir(parents=True, exist_ok=True)
        restore_database = restore_root / "data/runtime.sqlite3"
        restore_database.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(backup["database_path"]), restore_database)
        source_iteration = source_tree.parent
        restore_iteration = _tree_path(restore_root).parent
        restore_iteration.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_iteration, restore_iteration)
        restored_snapshot = _db_snapshot(restore_database, _tree_path(restore_root))

        threads = {
            scenario: f"omc:{PROJECT_ID}:{ITERATION_ID}:{node_id}:g1"
            for scenario, node_id in SCENARIOS.items()
        }
        checks = {
            "maintenance_window_isolated": maintenance["formal_service_stop_authorized"] is False,
            "new_standard_v2_iteration": (
                source_snapshot["tree"]["mode"] == "standard"
                and source_snapshot["tree"]["workflow_contract_version"] == 2
                and source_snapshot["tree"]["root_id"] == PARENT_ID
            ),
            "three_process_crashes_observed": all(
                run["crash_returncode"] == EXPECTED_CRASH_CODE for run in runs.values()
            ),
            "three_fresh_process_resumes_succeeded": all(
                run["resume_returncode"] == 0 for run in runs.values()
            ),
            "same_checkpoint_threads_recovered": all(
                runs[name]["crash"].get("thread_id") == threads[name]
                and runs[name]["resume"].get("thread_id") == threads[name]
                and runs[name]["resume"].get("checkpoint_before") is True
                for name in SCENARIOS
            ),
            "original_human_message_not_duplicated": all(
                runs[name]["crash"].get("human_messages") == 1
                and runs[name]["resume"].get("human_messages") == 1
                for name in SCENARIOS
            ),
            "dispatch_receipts_exactly_once": (
                len(source_snapshot["dispatch_intents"]) == 3
                and len({item["task_key"] for item in source_snapshot["dispatch_intents"]}) == 3
                and all(item["state"] == "started" for item in source_snapshot["dispatch_intents"])
            ),
            "executor_started_receipts_exactly_once": all(
                item["receipt"].get("executor_started") is True
                and bool(item["receipt"].get("executor_started_at"))
                for item in source_snapshot["dispatch_intents"]
            ),
            "side_effect_ledger_exactly_once": (
                len(source_snapshot["tool_ledger"]) == 3
                and all(item["status"] == "completed" for item in source_snapshot["tool_ledger"])
                and all(
                    runs[name]["resume"]["snapshot"].get("external_side_effect_count") == 1
                    for name in SCENARIOS
                )
            ),
            "execution_generation_unchanged": all(
                source_snapshot["tree"]["nodes"][name]["execution_generation"] == 1
                for name in SCENARIOS
            ),
            "all_checkpoint_threads_present": all(
                source_snapshot["checkpoints"].get(thread_id, 0) > 0
                for thread_id in threads.values()
            ),
            "no_duplicate_children": (
                len(source_snapshot["tree"]["parent_children"]) == 3
                and set(source_snapshot["tree"]["parent_children"]) == set(SCENARIOS.values())
            ),
            "explicit_acceptance_only": all(
                source_snapshot["tree"]["nodes"][name]["status"] == "accepted"
                and source_snapshot["tree"]["nodes"][name]["acceptance_audit"].get("decided_via") == "accept_child"
                for name in SCENARIOS
            ),
            "memory_outbox_preserved_pending": (
                len(source_snapshot["memory_outbox"]) == 3
                and all(item["status"] == "pending" and item["attempt"] == 0 for item in source_snapshot["memory_outbox"])
            ),
            "memory_source_refs_match_threads": all(
                item["payload"].get("source_thread_id")
                == threads[
                    next(name for name, node_id in SCENARIOS.items() if node_id == item["payload"].get("source_node_id"))
                ]
                for item in source_snapshot["memory_outbox"]
            ),
            "online_backup_verified": (
                backup["integrity_check_result"] == "ok"
                and backup["manifest"].get("backup_method") == "sqlite_online_backup_api"
                and backup["manifest"].get("quick_check_result") == "ok"
            ),
            "restore_integrity_ok": (
                restored_snapshot["quick_check"] == "ok"
                and restored_snapshot["integrity_check"] == "ok"
            ),
            "restore_opened_read_only": restored_snapshot["read_only_enforced"] is True,
            "restored_business_state_matches": (
                restored_snapshot["dispatch_intents"] == source_snapshot["dispatch_intents"]
                and restored_snapshot["tool_ledger"] == source_snapshot["tool_ledger"]
                and restored_snapshot["checkpoints"] == source_snapshot["checkpoints"]
                and restored_snapshot["memory_outbox"] == source_snapshot["memory_outbox"]
                and restored_snapshot["tree"] == source_snapshot["tree"]
            ),
        }
        formal_after = _formal_snapshot(repo)
        checks["formal_data_unchanged"] = formal_before == formal_after
        checks["formal_memory_outbox_not_consumed"] = formal_before.get("outbox") == formal_after.get("outbox")
        secret_paths = [
            Path(backup["database_path"]),
            Path(backup["manifest_path"]),
            restore_database,
            source_tree,
            _tree_path(restore_root),
        ]
        checks["credentials_not_persisted"] = not any(_contains_secret(path) for path in secret_paths)

        failed = [name for name, passed in checks.items() if not passed]
        maintenance.update({"status": "closed", "ended_at": _now(), "result": "passed" if not failed else "failed"})
        maintenance_path.write_text(json.dumps(maintenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = {
            "status": "passed" if not failed else "failed",
            "generated_at": _now(),
            "maintenance_window": maintenance,
            "data_root": str(data_root),
            "restore_root": str(restore_root),
            "runs": runs,
            "backup": backup,
            "source_snapshot": source_snapshot,
            "restored_snapshot": restored_snapshot,
            "checks": checks,
            "failed_checks": failed,
            "formal_before": formal_before,
            "formal_after": formal_after,
            "formal_outbox_touched": formal_before != formal_after,
            "formal_24h_launch_allowed": False,
        }
        _write_report(report_path, payload)
        print(json.dumps({"status": payload["status"], "failed_checks": failed}, ensure_ascii=False))
        return 0 if not failed else 1
    except Exception as exc:
        formal_after = _formal_snapshot(repo)
        maintenance.update({"status": "closed", "ended_at": _now(), "result": "failed"})
        maintenance_path.write_text(json.dumps(maintenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = {
            "status": "failed",
            "generated_at": _now(),
            "error": f"{type(exc).__name__}: {exc}",
            "maintenance_window": maintenance,
            "data_root": str(data_root),
            "restore_root": str(restore_root),
            "runs": runs,
            "formal_before": formal_before,
            "formal_after": formal_after,
            "formal_outbox_touched": formal_before != formal_after,
            "formal_24h_launch_allowed": False,
        }
        _write_report(report_path, payload)
        print(payload["error"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
