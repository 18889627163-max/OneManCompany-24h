from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_real_provider_429_gate_holds_restarts_and_recovers(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/check-real-provider-429-gate.py"
    data_root = tmp_path / "isolated-provider-data"
    report = tmp_path / "provider-report.json"

    completed = subprocess.run(
        [sys.executable, str(script), "--data-root", str(data_root), "--report", str(report)],
        cwd=repo,
        env={**os.environ, "OMC_AUTOMATION_ENABLED": "false"},
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["formal_outbox_touched"] is False
    assert payload["result"]["failed_checks"] == []
    assert all(payload["result"]["checks"].values())
    assert payload["result"]["hold"]["snapshot"]["node"]["status"] == "holding"
    assert payload["result"]["resume"]["snapshot"]["provider"]["status"] == "completed"
    assert payload["result"]["resume"]["side_effect_counter"] == 1
    assert "isolated-provider-gate-key" not in report.read_text(encoding="utf-8")


def test_real_provider_429_gate_rejects_formal_data_root(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/check-real-provider-429-gate.py"
    report = tmp_path / "provider-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--data-root",
            str(repo / ".onemancompany"),
            "--report",
            str(report),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["formal_outbox_touched"] is False
    assert "formal .onemancompany" in payload["error"]
