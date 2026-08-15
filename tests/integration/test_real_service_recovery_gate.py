from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _environment(repo: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def test_real_service_recovery_gate_crashes_resumes_and_restores_read_only(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/check-real-service-recovery-gate.py"
    data_root = tmp_path / "drill"
    restore_root = tmp_path / "restored"
    report = tmp_path / "report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--data-root",
            str(data_root),
            "--restore-root",
            str(restore_root),
            "--report",
            str(report),
        ],
        cwd=repo,
        env=_environment(repo),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["failed_checks"] == []
    assert all(payload["checks"].values())
    assert payload["formal_outbox_touched"] is False
    assert payload["formal_24h_launch_allowed"] is False
    assert payload["maintenance_window"]["formal_service_stop_authorized"] is False
    assert payload["maintenance_window"]["status"] == "closed"

    for scenario in ("dispatch", "executor_started", "side_effect"):
        run = payload["runs"][scenario]
        assert run["crash_returncode"] == 87
        assert run["resume_returncode"] == 0
        assert run["resume"]["checkpoint_before"] is True
        assert run["resume"]["human_messages"] == 1
        assert run["resume"]["snapshot"]["external_side_effect_count"] == 1
        assert run["resume"]["snapshot"]["node"]["acceptance_audit"]["decided_via"] == "accept_child"

    assert payload["restored_snapshot"]["read_only_enforced"] is True
    assert payload["restored_snapshot"]["integrity_check"] == "ok"
    assert len(payload["restored_snapshot"]["memory_outbox"]) == 3
    assert "sk-" not in report.read_text(encoding="utf-8")


def test_real_service_recovery_gate_rejects_formal_data_root(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/check-real-service-recovery-gate.py"
    report = tmp_path / "blocked-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--data-root",
            str(repo / ".onemancompany"),
            "--restore-root",
            str(tmp_path / "restore"),
            "--report",
            str(report),
        ],
        cwd=repo,
        env=_environment(repo),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["formal_outbox_touched"] is False
    assert payload["formal_24h_launch_allowed"] is False
    assert "formal .onemancompany" in payload["error"]
