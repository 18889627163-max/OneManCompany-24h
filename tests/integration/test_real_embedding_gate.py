from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


class _EmbeddingHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") != "/v1/embeddings":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        values = payload.get("input", [])
        if isinstance(values, str):
            values = [values]
        dimensions = int(payload.get("dimensions") or 4)
        data = []
        for index, text in enumerate(values):
            lowered = str(text).lower()
            base = [
                3.0 if "checkpoint" in lowered else 0.2,
                3.0 if "replay" in lowered or "side effect" in lowered else 0.2,
                2.0 if "tasktree" in lowered or "receipt" in lowered else 0.1,
                1.0,
            ]
            vector = (base + [0.1] * dimensions)[:dimensions]
            norm = math.sqrt(sum(item * item for item in vector)) or 1.0
            data.append({"object": "embedding", "index": index, "embedding": [item / norm for item in vector]})
        body = json.dumps(
            {
                "object": "list",
                "data": data,
                "model": payload.get("model", "test-embedding"),
                "usage": {"prompt_tokens": len(values), "total_tokens": len(values)},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - stdlib handler API
        return


@pytest.fixture
def embedding_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _gate_env(base_url: str) -> dict[str, str]:
    return {
        **os.environ,
        "OMC_MEMORY_EMBEDDING_BASE_URL": base_url,
        "OMC_MEMORY_EMBEDDING_API_KEY": "test-key-not-persisted",
        "OMC_MEMORY_EMBEDDING_MODEL": "test-embedding-model",
        "OMC_MEMORY_EMBEDDING_DIMENSIONS": "4",
        "OMC_MEMORY_INDEX_VERSION": "test-v1",
    }


def test_real_embedding_gate_runs_only_in_fresh_isolated_data_root(tmp_path, embedding_server):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/check-real-embedding-gate.py"
    data_root = tmp_path / "isolated-data"
    report = tmp_path / "report.json"

    completed = subprocess.run(
        [sys.executable, str(script), "--data-root", str(data_root), "--report", str(report)],
        cwd=repo,
        env=_gate_env(embedding_server),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(report.read_text())
    assert payload["status"] == "passed"
    assert payload["formal_outbox_touched"] is False
    assert payload["result"]["outbox_count"] == 0
    assert payload["result"]["vector_count"] >= 4
    assert payload["result"]["checks"]["outsider_cannot_read_project"] is True
    assert "test-key-not-persisted" not in report.read_text()


def test_real_embedding_gate_rejects_formal_data_root(tmp_path, embedding_server):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts/check-real-embedding-gate.py"
    report = tmp_path / "report.json"

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
        env=_gate_env(embedding_server),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(report.read_text())
    assert payload["status"] == "blocked"
    assert payload["formal_outbox_touched"] is False
    assert "formal .onemancompany" in payload["error"]
