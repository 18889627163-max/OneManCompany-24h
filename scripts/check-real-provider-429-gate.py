#!/usr/bin/env python3
"""Run an isolated real HTTP 429 -> restart -> checkpoint recovery gate.

The endpoint is a deterministic local OpenAI-compatible HTTP server. This makes
an actual ChatOpenAI network request and an actual HTTP 429 reproducible without
consuming production quota or intentionally attacking a cloud provider. All
runtime state lives under a fresh temporary data root; formal OMC data is only
hashed before and after the gate.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMAL_DATA_ROOT = (ROOT / ".onemancompany").resolve()
DEFAULT_REPORT = ROOT / "docs/24h-work-mode/reports/REAL-PROVIDER-429-GATE-REPORT.json"
GATE_KEY = "isolated-provider-gate-key"


def _bootstrap_venv() -> None:
    try:
        import httpx  # noqa: F401
        import langchain_openai  # noqa: F401
        import sqlite_vec  # noqa: F401
    except ModuleNotFoundError:
        venv_python = ROOT / ".venv/bin/python"
        if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _fresh_data_root(requested: Path | None) -> tuple[Path, bool]:
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="omc-provider-429-gate-")).resolve(), True
    path = requested.expanduser().resolve()
    if path == FORMAL_DATA_ROOT or _is_relative_to(path, FORMAL_DATA_ROOT):
        raise ValueError("provider 429 gate data root must not be the formal .onemancompany tree")
    if path.exists() and any(path.iterdir()):
        raise ValueError("provider 429 gate requires a new or empty data root")
    path.mkdir(parents=True, exist_ok=True)
    return path, False


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _formal_hashes() -> dict[str, str | None]:
    legacy_iteration_files = sorted(
        FORMAL_DATA_ROOT.glob("company/business/projects/*/iterations/iter_009.yaml")
    )
    task_tree_files = sorted(
        FORMAL_DATA_ROOT.glob("company/business/projects/*/iterations/iter_009/task_tree.yaml")
    )
    return {
        "runtime_sqlite": _sha256(FORMAL_DATA_ROOT / "data/runtime.sqlite3"),
        "legacy_iter_009_yaml": (
            _sha256(legacy_iteration_files[0]) if len(legacy_iteration_files) == 1 else None
        ),
        "iter_009_task_tree": _sha256(task_tree_files[0]) if len(task_tree_files) == 1 else None,
        "active_employee_00010": _sha256(
            FORMAL_DATA_ROOT / "company/human_resource/employees/00010/profile.yaml"
        ),
        "archived_employee_00010": _sha256(
            FORMAL_DATA_ROOT / "company/human_resource/ex-employees/00010/profile.yaml"
        ),
    }


class _ServerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.chat_mode = "holding"
        self.chat_attempts = 0
        self.request_order: list[str] = []
        self.business_started = threading.Event()
        self.release_business = threading.Event()
        self.memory_seen = threading.Event()

    def append(self, name: str) -> None:
        with self.lock:
            self.request_order.append(name)


class _ProviderHandler(BaseHTTPRequestHandler):
    server_version = "OMCProvider429Gate/1.0"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("content-length", "0") or "0")
        if length:
            self.rfile.read(length)
        state: _ServerState = self.server.gate_state  # type: ignore[attr-defined]
        if self.path.endswith("/chat/completions"):
            with state.lock:
                state.chat_attempts += 1
                mode = state.chat_mode
                state.request_order.append(f"chat:{mode}")
            if mode == "holding":
                self._json(429, {
                    "error": {
                        "message": "Concurrency limit exceeded for isolated provider gate",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                })
                return
            self._json(200, {
                "id": "chatcmpl-provider-gate",
                "object": "chat.completion",
                "created": 0,
                "model": "provider-gate-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "provider recovered"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
            return
        if self.path == "/priority/business":
            state.append("priority:business:start")
            state.business_started.set()
            state.release_business.wait(timeout=10)
            state.append("priority:business:end")
            self._json(200, {"status": "business-complete"})
            return
        if self.path == "/priority/memory":
            state.append("priority:memory:start")
            state.memory_seen.set()
            self._json(200, {"status": "memory-complete"})
            return
        self._json(404, {"error": {"message": "not found"}})

    def log_message(self, format, *args):  # noqa: A002 - stdlib handler API
        return


def _run_worker(phase: str, data_root: Path, base_url: str, output: Path) -> dict[str, Any]:
    worker = ROOT / "tests/integration/provider_429_gate_worker.py"
    env = {
        **os.environ,
        "OMC_DATA_ROOT": str(data_root),
        "OMC_MEMORY_ENABLED": "false",
        "OMC_AUTOMATION_ENABLED": "false",
        "OMC_RESTORE_PERSISTED_TASKS": "false",
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(worker),
            "--phase",
            phase,
            "--data-root",
            str(data_root),
            "--base-url",
            base_url,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"provider gate worker {phase} failed with {completed.returncode}: "
            f"{completed.stderr[-2000:]}{completed.stdout[-2000:]}"
        )
    return json.loads(output.read_text(encoding="utf-8"))


async def _priority_gate(data_root: Path, base_url: str, state: _ServerState) -> dict[str, Any]:
    import httpx

    from onemancompany.core.provider_gateway import ProviderGateway, ProviderPriority
    from onemancompany.core.runtime_storage import RuntimeStorage

    storage = RuntimeStorage(data_root / "data/runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage, default_concurrency=1, transient_retry_limit_for_call=0)
    await gateway.start()
    client = httpx.AsyncClient(timeout=10)

    async def post(path: str) -> dict[str, Any]:
        response = await client.post(base_url.removesuffix("/v1") + path, json={"gate": True})
        response.raise_for_status()
        return response.json()

    business_context = {
        "provider": "priority-business",
        "credential_fingerprint": "isolated",
        "account_or_model_pool": "chat",
        "request_id": "priority-business-request",
        "node_id": "priority-business-node",
    }
    memory_context = {
        "provider": "priority-memory",
        "credential_fingerprint": "isolated",
        "account_or_model_pool": "embedding",
        "request_id": "priority-memory-request",
        "node_id": "memory-worker",
        "transient_retry_limit_for_call": 0,
    }

    async def business_call():
        return await gateway.invoke(
            business_context,
            ProviderPriority.BUSINESS,
            lambda: post("/priority/business"),
        )

    async def memory_worker_call():
        await gateway.wait_for_background_turn(ProviderPriority.EMBEDDING)
        return await gateway.invoke(
            memory_context,
            ProviderPriority.EMBEDDING,
            lambda: post("/priority/memory"),
        )

    try:
        business = asyncio.create_task(business_call())
        started = await asyncio.to_thread(state.business_started.wait, 5)
        if not started:
            raise RuntimeError("priority business request did not reach the HTTP server")
        memory = asyncio.create_task(memory_worker_call())
        await asyncio.sleep(0.15)
        memory_withheld = not state.memory_seen.is_set()
        metrics_while_business = await gateway.metrics()
        state.release_business.set()
        business_result, memory_result = await asyncio.gather(business, memory)
        return {
            "memory_withheld_while_business_active": memory_withheld,
            "business_result": business_result,
            "memory_result": memory_result,
            "metrics_while_business": metrics_while_business,
            "request_order": list(state.request_order),
        }
    finally:
        state.release_business.set()
        await client.aclose()
        await gateway.stop()
        await storage.close()


def _isolated_db_checks(data_root: Path) -> dict[str, Any]:
    import sqlite3

    db = data_root / "data/runtime.sqlite3"
    conn = sqlite3.connect(db)
    try:
        outbox = int(conn.execute("SELECT COUNT(*) FROM memory_outbox").fetchone()[0])
        provider = int(conn.execute("SELECT COUNT(*) FROM provider_queue").fetchone()[0])
        audits = int(conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type IN "
            "('provider_capacity_holding','provider_capacity_recovered')"
        ).fetchone()[0])
    finally:
        conn.close()
    raw = db.read_bytes()
    return {
        "outbox_count": outbox,
        "provider_request_count": provider,
        "provider_audit_count": audits,
        "api_key_not_persisted": GATE_KEY.encode("utf-8") not in raw,
    }


def _checks(
    hold: dict[str, Any],
    resume: dict[str, Any],
    priority: dict[str, Any],
    isolated: dict[str, Any],
    state: _ServerState,
    formal_unchanged: bool,
) -> dict[str, bool]:
    h = hold["snapshot"]
    before = resume["before_resume"]
    final = resume["snapshot"]
    frontend = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
    order = priority["request_order"]
    try:
        business_end = order.index("priority:business:end")
        memory_start = order.index("priority:memory:start")
    except ValueError:
        business_end = memory_start = -1
    return {
        "real_chat_http_429_observed": hold.get("provider_exception_type") in {
            "RateLimitError", "APIStatusError"
        } and state.chat_attempts == 2,
        "tasknode_stayed_holding": h["node"]["status"] == "holding"
        and h["node"]["hold_reason"] == "provider_capacity"
        and h["node"]["checkpoint_status"] == "waiting_provider",
        "attempt_and_retry_persisted_across_restart": h["provider"]["attempt"] == 1
        and bool(h["provider"]["next_retry_at"])
        and before["provider"]["attempt"] == h["provider"]["attempt"]
        and before["provider"]["next_retry_at"] == h["provider"]["next_retry_at"]
        and before["retry"] == h["retry"],
        "formal_task_precedes_memory": business_end >= 0 and memory_start > business_end,
        "memory_worker_yielded_provider_slot": bool(
            priority["memory_withheld_while_business_active"]
        ),
        "same_checkpoint_thread_recovered": resume["checkpoint_before"] is True
        and h["node"]["checkpoint_thread_id"] == resume["thread_id"]
        and before["node"]["checkpoint_thread_id"] == resume["thread_id"]
        and final["node"]["checkpoint_thread_id"] == resume["thread_id"]
        and final["node"]["status"] == "processing"
        and final["node"]["checkpoint_status"] == "active",
        "provider_request_reused_and_completed": h["provider"]["request_id"]
        == before["provider"]["request_id"]
        == final["provider"]["request_id"]
        and final["provider"]["status"] == "completed"
        and final["provider"]["attempt"] == 1
        and final["provider"]["next_retry_at"] is None
        and final["retry"]["attempt"] == 1
        and final["retry"]["next_retry_at"] is None
        and final["retry"]["last_error_class"] is None,
        "dispatch_not_duplicated": h["dispatch_count"] == before["dispatch_count"]
        == final["dispatch_count"] == 1,
        "side_effect_not_duplicated": hold["side_effect_counter"]
        == resume["side_effect_counter"] == 1
        and h["tool_ledger_count"] == before["tool_ledger_count"]
        == final["tool_ledger_count"] == 1,
        "original_human_message_not_duplicated": resume["human_messages"] == 1,
        "recovery_ui_fields_visible": h["node"]["hold_reason"] == "provider_capacity"
        and bool(h["node"]["next_retry_at"])
        and "等待模型容量" in frontend
        and "taskAttentionLabel" in frontend,
        "recovery_ui_cleared": final["node"]["hold_reason"] == ""
        and final["node"]["next_retry_at"] == "",
        "holding_and_recovery_audited": isolated["provider_audit_count"] == 2,
        "credentials_not_persisted": isolated["api_key_not_persisted"],
        "formal_data_unchanged": formal_unchanged,
        "formal_memory_outbox_not_consumed": isolated["outbox_count"] == 0,
    }


def main() -> int:
    _bootstrap_venv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()
    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    created_temp = False
    data_root: Path | None = None
    before_formal = _formal_hashes()
    server = None
    thread = None
    started_at = _now()
    try:
        data_root, created_temp = _fresh_data_root(args.data_root)
        state = _ServerState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        server.gate_state = state  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}/v1"

        hold = _run_worker("hold", data_root, base_url, data_root / "hold.json")
        state.chat_mode = "success"
        resume = _run_worker("resume", data_root, base_url, data_root / "resume.json")
        priority = asyncio.run(_priority_gate(data_root, base_url, state))
        isolated = _isolated_db_checks(data_root)
        after_formal = _formal_hashes()
        checks = _checks(
            hold, resume, priority, isolated, state, before_formal == after_formal
        )
        failed = sorted(name for name, passed in checks.items() if not passed)
        payload = {
            "status": "passed" if not failed else "failed",
            "gate": "real-provider-http-429-recovery",
            "started_at": started_at,
            "completed_at": _now(),
            "data_root": str(data_root),
            "endpoint": "isolated-loopback-openai-compatible",
            "formal_outbox_touched": False,
            "result": {
                "checks": checks,
                "failed_checks": failed,
                "hold": hold,
                "resume": resume,
                "priority": priority,
                "isolated_database": isolated,
                "chat_http_attempts": state.chat_attempts,
                "formal_hashes_before": before_formal,
                "formal_hashes_after": after_formal,
            },
        }
        report_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if GATE_KEY in report_text:
            raise RuntimeError("sanitization failure: gate API key appeared in report")
        report_path.write_text(report_text, encoding="utf-8")
        print(json.dumps({
            "status": payload["status"],
            "report": str(report_path),
            "failed_checks": failed,
        }, ensure_ascii=False))
        return 0 if not failed else 1
    except Exception as exc:
        payload = {
            "status": "blocked",
            "gate": "real-provider-http-429-recovery",
            "started_at": started_at,
            "completed_at": _now(),
            "formal_outbox_touched": False,
            "error": str(exc).replace(GATE_KEY, "<redacted>"),
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        if created_temp and data_root is not None and not args.keep_data:
            shutil.rmtree(data_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
