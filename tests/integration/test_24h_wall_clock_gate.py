from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from onemancompany.core.runtime_storage import RuntimeStorage


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _create_runtime_database(path: Path) -> None:
    storage = RuntimeStorage(path)
    await storage.initialize()
    await storage.enqueue_memory_outbox(
        event_id="formal-pending-event",
        namespace=("employee", "00003", "episodic"),
        memory_key="formal-node:episodic",
        payload={"text": "do not consume"},
    )
    await storage.close()


def _formal_root(tmp_path: Path) -> Path:
    root = tmp_path / "formal-data"
    database = root / "data/runtime.sqlite3"
    database.parent.mkdir(parents=True)
    asyncio.run(_create_runtime_database(database))

    project = root / "company/business/projects/18b1e9d4a1fc/iterations"
    (project / "iter_009").mkdir(parents=True)
    (project / "iter_009.yaml").write_text("legacy: protected\n", encoding="utf-8")
    (project / "iter_009/task_tree.yaml").write_text(
        "project_id: protected/iter_009\nmode: standard\nworkflow_contract_version: 2\nnodes: []\n",
        encoding="utf-8",
    )
    for employee_id in ("00001", "00003", "00006", "00007", "00008", "00009"):
        employee = root / f"company/human_resource/employees/{employee_id}"
        employee.mkdir(parents=True)
        (employee / "profile.yaml").write_text(
            f"employee_number: '{employee_id}'\nname: Employee {employee_id}\nrole: Test\nskills: []\n",
            encoding="utf-8",
        )
    return root


def test_prepare_wall_clock_gate_creates_isolated_recoverable_run(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/run-24h-wall-clock-gate.py"
    formal_root = _formal_root(tmp_path)
    run_root = tmp_path / "wall-clock-run"
    protected = [
        formal_root / "data/runtime.sqlite3",
        formal_root / "company/business/projects/18b1e9d4a1fc/iterations/iter_009.yaml",
        formal_root / "company/business/projects/18b1e9d4a1fc/iterations/iter_009/task_tree.yaml",
    ]
    before = {str(path): _sha256(path) for path in protected}

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "prepare",
            "--repo-root",
            str(repo),
            "--formal-data-root",
            str(formal_root),
            "--run-root",
            str(run_root),
            "--duration-seconds",
            "86400",
        ],
        cwd=repo,
        env={**os.environ, "OMC_MEMORY_EMBEDDING_API_KEY": "must-not-be-persisted"},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    manifest = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((run_root / "state.json").read_text(encoding="utf-8"))
    schedule = json.loads((run_root / "fault-schedule.json").read_text(encoding="utf-8"))
    tree = yaml.safe_load(
        (
            run_root
            / "isolated-data/company/business/projects/wall-clock-drill-20260815/iterations/iter_001/task_tree.yaml"
        ).read_text(encoding="utf-8")
    )

    assert manifest["status"] == "prepared"
    assert manifest["formal_data_root_touched"] is False
    assert manifest["baseline"]["runtime_backup"]["integrity_check"] == "ok"
    assert manifest["baseline"]["outbox"]["pending"] == 1
    assert manifest["baseline"]["outbox"]["attempted"] == 0
    assert state["status"] == "prepared"
    assert state["duration_seconds"] == 86400
    assert [item["fault"] for item in schedule["events"]] == [
        "provider_429",
        "embedding_unavailable",
        "backend_restart",
        "sqlite_lock",
    ]
    assert tree["mode"] == "standard"
    assert tree["workflow_contract_version"] == 2
    assert tree["project_id"] == "wall-clock-drill-20260815/iter_001"
    assert tree["nodes"][0]["checkpoint_thread_id"].startswith(
        "omc:wall-clock-drill-20260815:iter_001:"
    )
    assert (run_root / "isolated-data/data/runtime.sqlite3").exists()
    assert (run_root / "evidence/events.jsonl").exists()
    assert before == {str(path): _sha256(path) for path in protected}
    serialized = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in run_root.rglob("*.json")
    )
    assert "must-not-be-persisted" not in serialized


def test_prepare_wall_clock_gate_rejects_formal_run_root(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/run-24h-wall-clock-gate.py"
    formal_root = _formal_root(tmp_path)
    report = formal_root / "manifest.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "prepare",
            "--repo-root",
            str(repo),
            "--formal-data-root",
            str(formal_root),
            "--run-root",
            str(formal_root / "wall-clock-run"),
            "--duration-seconds",
            "86400",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert not report.exists()
    assert "outside formal data root" in completed.stderr


def _fake_backend_script(path: Path) -> None:
    path.write_text(
        """
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            body = json.dumps({
                "status": "healthy",
                "checkpoint_store": "healthy",
                "memory_store": "healthy",
                "embedding": "degraded",
                "memory_worker_backlog": 1,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        return

ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_run_wall_clock_gate_resumes_monitoring_executes_faults_and_finalizes(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/run-24h-wall-clock-gate.py"
    formal_root = _formal_root(tmp_path)
    run_root = tmp_path / "wall-clock-run"
    fake_backend = tmp_path / "fake_backend.py"
    _fake_backend_script(fake_backend)
    protected = [
        formal_root / "data/runtime.sqlite3",
        formal_root / "company/business/projects/18b1e9d4a1fc/iterations/iter_009.yaml",
        formal_root / "company/business/projects/18b1e9d4a1fc/iterations/iter_009/task_tree.yaml",
    ]
    before = {str(path): _sha256(path) for path in protected}

    prepared = subprocess.run(
        [
            sys.executable,
            str(script),
            "prepare",
            "--repo-root",
            str(repo),
            "--formal-data-root",
            str(formal_root),
            "--run-root",
            str(run_root),
            "--duration-seconds",
            "6",
            "--sample-interval-seconds",
            "1",
            "--port",
            "0",
            "--test-mode",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr + prepared.stdout

    # Simulate a supervisor process dying after durably marking a fault as
    # running but before it could write completion evidence. A resumed
    # supervisor must requeue the bounded isolated fault, not skip it forever.
    interrupted_schedule_path = run_root / "fault-schedule.json"
    interrupted_schedule = json.loads(interrupted_schedule_path.read_text(encoding="utf-8"))
    interrupted_schedule["events"][0]["status"] = "running"
    interrupted_schedule["events"][0]["started_at"] = "2026-08-15T00:00:00+00:00"
    interrupted_schedule_path.write_text(
        json.dumps(interrupted_schedule, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    executed = subprocess.run(
        [
            sys.executable,
            str(script),
            "run",
            "--run-root",
            str(run_root),
            "--backend-command",
            f"{sys.executable} {fake_backend}",
            "--fault-duration-seconds",
            "0.2",
            "--test-mode",
        ],
        cwd=repo,
        env={**os.environ, "OMC_MEMORY_EMBEDDING_API_KEY": "must-not-be-persisted"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert executed.returncode == 0, executed.stderr + executed.stdout

    status = subprocess.run(
        [sys.executable, str(script), "status", "--run-root", str(run_root)],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert status.returncode == 0, status.stderr + status.stdout
    status_payload = json.loads(status.stdout)
    state = json.loads((run_root / "state.json").read_text(encoding="utf-8"))
    schedule = json.loads((run_root / "fault-schedule.json").read_text(encoding="utf-8"))
    report = json.loads((run_root / "final-report.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (run_root / "evidence/events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert status_payload["status"] == "completed"
    assert state["status"] == "completed"
    assert state["backend"]["restart_count"] >= 1
    assert all(item["status"] == "passed" for item in schedule["events"])
    assert [item["fault"] for item in schedule["events"]] == [
        "provider_429",
        "embedding_unavailable",
        "backend_restart",
        "sqlite_lock",
    ]
    assert report["status"] == "passed"
    assert report["test_mode"] is True
    assert report["formal_24h_launch_allowed"] is False
    assert all(report["checks"].values())
    assert any(item["event"] == "monitoring_sample" for item in events)
    assert any(item["event"] == "backend_restarted" for item in events)
    assert {
        item.get("fault")
        for item in events
        if item.get("event") == "fault_completed"
    } == {"provider_429", "embedding_unavailable", "backend_restart", "sqlite_lock"}
    assert before == {str(path): _sha256(path) for path in protected}
    serialized = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in run_root.rglob("*.json*")
    )
    assert "must-not-be-persisted" not in serialized

    finalized = subprocess.run(
        [sys.executable, str(script), "finalize", "--run-root", str(run_root)],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert finalized.returncode == 0, finalized.stderr + finalized.stdout
