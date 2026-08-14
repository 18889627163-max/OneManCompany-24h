from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(worker: Path, phase: str, data_root: Path, output: Path, env: dict):
    return subprocess.run(
        [sys.executable, str(worker), phase, str(data_root), str(output)],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_standard_v2_subprocess_crash_resumes_same_checkpoint_without_replaying_side_effect(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    worker = Path(__file__).with_name("recovery_worker.py")
    data_root = tmp_path / "isolated-omc-data"
    crash_output = tmp_path / "crash.json"
    resume_output = tmp_path / "resume.json"
    side_counter = tmp_path / "side-effect-count.txt"
    final_counter = tmp_path / "final-count.txt"
    env = {
        **os.environ,
        "PYTHONPATH": str(repo / "src"),
        "OMC_DATA_ROOT": str(data_root),
        "RECOVERY_SIDE_EFFECT_COUNTER": str(side_counter),
        "RECOVERY_FINAL_COUNTER": str(final_counter),
    }

    crashed = _run(worker, "crash", data_root, crash_output, env)
    assert crashed.returncode == 87, crashed.stderr
    crash = json.loads(crash_output.read_text())
    assert crash == {"messages": 2, "checkpoint": True, "ledger_status": "completed"}
    assert side_counter.read_text() == "1"
    assert not final_counter.exists()

    resumed = _run(worker, "resume", data_root, resume_output, env)
    assert resumed.returncode == 0, resumed.stderr
    payload = json.loads(resume_output.read_text())
    assert payload["checkpoint_before"] is True
    assert payload["human_messages"] == 1
    assert payload["side_effect_messages"] == 1
    assert payload["finalize_messages"] == 1
    assert payload["thread_id"] == "omc:recovery-project:iter_001:recovery-node:g1"
    assert side_counter.read_text() == "1"
    assert final_counter.read_text() == "1"
