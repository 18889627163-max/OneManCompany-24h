from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_provider_429_holding_metadata_survives_process_restart_and_resumes(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    worker = Path(__file__).with_name("provider_recovery_worker.py")
    data_root = tmp_path / "isolated-omc-data"
    hold_output = tmp_path / "hold.json"
    resume_output = tmp_path / "resume.json"
    counter = tmp_path / "provider-resume-count.txt"
    env = {
        **os.environ,
        "PYTHONPATH": str(repo / "src"),
        "OMC_DATA_ROOT": str(data_root),
        "PROVIDER_RESUME_COUNTER": str(counter),
    }

    held = subprocess.run(
        [sys.executable, str(worker), "hold", str(data_root), str(hold_output)],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert held.returncode == 88, held.stderr
    holding = json.loads(hold_output.read_text())
    assert holding["status"] == "holding"
    assert holding["attempt"] == 1
    assert holding["next_retry_at"]

    resumed = subprocess.run(
        [sys.executable, str(worker), "resume", str(data_root), str(resume_output)],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    payload = json.loads(resume_output.read_text())
    assert payload == {
        "result": "ok",
        "status": "completed",
        "attempt": 1,
        "next_retry_at": None,
        "retry_attempt": 1,
    }
    assert counter.read_text() == "1"
