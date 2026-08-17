#!/usr/bin/env python3
"""Run the machine-readable standard-v2 P0 gate without touching formal tasks.

This gate verifies durable runtime, isolated crash/resume recovery, provider
holding/resume, checkpoint reconciliation, dispatch idempotency, explicit
acceptance/Closure Gate, memory authority boundaries, formal employee
configuration, atomic work-principles application, automation manifest, and
backup assets. Passing it does not by itself authorize formal 24-hour launch;
real cloud-provider, real-service restart, embedding, device-smoke, and 24-hour
wall-clock drills remain separate gates.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except ModuleNotFoundError:
    _venv_python = Path(__file__).resolve().parents[1] / ".venv/bin/python"
    if _venv_python.is_file() and Path(sys.executable).resolve() != _venv_python.resolve():
        os.execv(str(_venv_python), [str(_venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    raise

ROOT = Path(__file__).resolve().parents[1]
ITER_009 = ROOT / ".onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009"
DEFAULT_REPORT = ROOT / "docs/24h-work-mode/reports/P0-GATE-REPORT.json"

TEST_GROUPS: dict[str, list[str]] = {
    "runtime_checkpoint_provider": [
        "tests/unit/core/test_execution_runtime.py",
        "tests/unit/core/test_provider_gateway.py",
        "tests/unit/core/test_runtime_storage.py",
    ],
    "dispatch_receipts_and_idempotency": [
        "tests/unit/agents/test_dispatch_idempotency.py",
        "tests/integration/test_dispatch_assignment_flow.py",
    ],
    "explicit_acceptance_and_closure": [
        "tests/unit/agents/test_explicit_acceptance_v2.py",
        "tests/unit/core/test_standard_v2_review_omission.py",
        "tests/integration/test_formal_dispatch_checker.py",
    ],
    "memory_authority_and_secret_boundaries": [
        "tests/unit/core/test_memory_service.py",
        "tests/unit/core/test_memory_policy_additions.py",
        "tests/unit/api/test_memory_admin_api.py",
        "tests/unit/agents/test_checkpoint_secret_filter.py",
    ],
    "automation_registration_contract": [
        "tests/unit/core/test_automation_manifest.py",
    ],
    "isolated_recovery_crash_resume_and_reconciliation": [
        "tests/integration/test_checkpoint_crash_resume.py",
        "tests/integration/test_provider_holding_resume.py",
        "tests/integration/test_checkpoint_reconciler.py",
        "tests/unit/core/test_checkpoint_reconciler.py",
    ],
}

EXPECTED_EMPLOYEES = {
    "00003": ("COO", "gpt-5.6-sol"),
    "00006": ("Senior Backend Engineer", "gpt-5.6-sol"),
    "00007": ("Full-Stack Engineer", "gpt-5.6-sol"),
    "00008": ("DevOps/SRE Engineer", "gpt-5.6-sol"),
    "00009": ("QA Lead", "gpt-5.6-sol"),
    "00010": ("Tech Lead", "gpt-5.6-sol"),
    "00011": ("Mid-level Backend Engineer", "gpt-5.6-sol"),
    "00012": ("Automation Test Engineer", "gpt-5.6-sol"),
}


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "missing"
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _check(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        detail = fn()
        return {"name": name, "status": "passed", "detail": detail}
    except Exception as exc:  # noqa: BLE001 - report all gate failures uniformly
        return {"name": name, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"}


def _import_contract() -> dict[str, str]:
    modules = {
        "AsyncSqliteSaver": "langgraph.checkpoint.sqlite.aio",
        "AsyncSqliteStore": "langgraph.store.sqlite.aio",
    }
    resolved: dict[str, str] = {}
    for symbol, module_name in modules.items():
        module = importlib.import_module(module_name)
        value = getattr(module, symbol)
        resolved[symbol] = f"{value.__module__}.{value.__name__}"
    return resolved


def _employee_contract() -> dict[str, Any]:
    root = ROOT / ".onemancompany/company/human_resource/employees"
    profiles = []
    for number in range(1, 13):
        employee_id = f"{number:05d}"
        path = root / employee_id / "profile.yaml"
        if not path.is_file():
            raise AssertionError(f"missing formal profile: {employee_id}")
        profiles.append(employee_id)
    aligned = {}
    for employee_id, (role, model) in EXPECTED_EMPLOYEES.items():
        data = yaml.safe_load((root / employee_id / "profile.yaml").read_text(encoding="utf-8")) or {}
        actual = (data.get("role"), data.get("llm_model"))
        if actual != (role, model):
            raise AssertionError(f"{employee_id}: expected {(role, model)!r}, got {actual!r}")
        aligned[employee_id] = {"role": role, "model": model}
    return {"formal_profiles": profiles, "aligned": aligned}


def _work_principles_contract() -> dict[str, Any]:
    employee_root = ROOT / ".onemancompany/company/human_resource/employees"
    revision_path = employee_root / ".work-principles-revision.yaml"
    revision = yaml.safe_load(revision_path.read_text(encoding="utf-8")) or {}
    checked = []
    for record in revision.get("files", []):
        employee_id = str(record["employee_id"])
        source = ROOT / str(record["source"])
        runtime = employee_root / employee_id / "work_principles.md"
        if not source.is_file() or not runtime.is_file():
            raise AssertionError(f"missing work principles for {employee_id}")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        runtime_hash = hashlib.sha256(runtime.read_bytes()).hexdigest()
        if source_hash != record.get("sha256") or runtime_hash != source_hash:
            raise AssertionError(f"work principles hash mismatch for {employee_id}")
        checked.append(employee_id)
    if checked != [f"{number:05d}" for number in range(2, 13)]:
        raise AssertionError(f"expected 00002-00012, got {checked}")
    return {"revision": revision.get("revision"), "employees": checked}


def _automation_contract() -> dict[str, Any]:
    manifest_path = ROOT / "docs/automation/cron-tasks.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    tasks = data.get("cron_tasks") or []
    if len(tasks) != 13:
        raise AssertionError(f"expected 13 automation tasks, got {len(tasks)}")
    ids = [str(task.get("id")) for task in tasks]
    if len(set(ids)) != len(ids):
        raise AssertionError("duplicate automation ids")
    employee_root = ROOT / ".onemancompany/company/human_resource/employees"
    missing = [
        str(task.get("employee_id"))
        for task in tasks
        if not (employee_root / str(task.get("employee_id")) / "profile.yaml").is_file()
    ]
    if missing:
        raise AssertionError(f"automation references missing employees: {missing}")
    return {"count": len(tasks), "ids": ids}


def _backup_assets_contract() -> dict[str, Any]:
    paths = [
        ROOT / "docs/automation/backup-scripts/backup-all.sh",
        ROOT / "docs/automation/backup-scripts/restore.sh",
        ROOT / "scripts/check-system-ready.sh",
        ROOT / "scripts/monitor-24h-mode.sh",
        ROOT / "scripts/verify-24h-mode.sh",
    ]
    for path in paths:
        if not path.is_file():
            raise AssertionError(f"missing operational script: {path.relative_to(ROOT)}")
        if not os.access(path, os.X_OK):
            raise AssertionError(f"operational script is not executable: {path.relative_to(ROOT)}")
    return {"scripts": [str(path.relative_to(ROOT)) for path in paths]}


def _run_pytest(group: str, files: list[str], timeout: int) -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q", *files, f"--timeout={timeout}"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(timeout * 4, 120),
        check=False,
    )
    output = completed.stdout.strip()
    return {
        "name": group,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "returncode": completed.returncode,
        "summary": output.splitlines()[-1] if output else "no output",
        "output_tail": output.splitlines()[-20:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    iter_before = _tree_hash(ITER_009)
    static_checks = [
        _check("sqlite_langgraph_import_contract", _import_contract),
        _check("formal_employee_configuration", _employee_contract),
        _check("atomic_work_principles", _work_principles_contract),
        _check("automation_manifest", _automation_contract),
        _check("operational_scripts", _backup_assets_contract),
    ]
    test_results = [
        _run_pytest(group, files, args.timeout) for group, files in TEST_GROUPS.items()
    ]
    iter_after = _tree_hash(ITER_009)
    protected = {
        "name": "iter_009_read_only_protection",
        "status": "passed" if iter_before != "missing" and iter_before == iter_after else "failed",
        "before": iter_before,
        "after": iter_after,
    }
    all_results = [*static_checks, *test_results, protected]
    passed = all(item["status"] == "passed" for item in all_results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": "standard_v2_p0",
        "status": "passed" if passed else "failed",
        "formal_24h_launch_allowed": False,
        "launch_note": (
            "P0 and isolated recovery gates passed; real cloud-provider, real-service restart, embedding, device-smoke, and 24-hour wall-clock drills remain required."
            if passed
            else "P0 failed; do not create the final formal standard-v2 iteration."
        ),
        "results": all_results,
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
