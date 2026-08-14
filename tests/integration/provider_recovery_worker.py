"""Subprocess worker for durable provider holding/resume verification."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from onemancompany.core.provider_gateway import ProviderGateway, ProviderPriority
from onemancompany.core.runtime_storage import RuntimeStorage


REQUEST_ID = "provider-recovery-1"


async def run(phase: str, data_root: Path, output: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    storage = RuntimeStorage(data_root / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(
        storage,
        transient_retry_limit_for_call=0 if phase == "hold" else None,
        max_backoff_seconds=0.01,
    )
    context = {
        "request_id": REQUEST_ID,
        "provider": "probe",
        "credential_fingerprint": "isolated-test-credential",
        "node_id": "provider-node",
    }

    if phase == "hold":
        async def fail():
            raise RuntimeError("HTTP 429 Concurrency limit exceeded for user")

        try:
            await gateway.invoke(context, ProviderPriority.BUSINESS, fail)
        except RuntimeError:
            pass
        row = await storage.fetchone(
            "SELECT status,attempt,next_retry_at FROM provider_queue WHERE request_id=?",
            (REQUEST_ID,),
        )
        output.write_text(json.dumps({
            "status": row[0],
            "attempt": row[1],
            "next_retry_at": row[2],
        }))
        os._exit(88)

    counter = Path(os.environ["PROVIDER_RESUME_COUNTER"])

    async def succeed():
        count = int(counter.read_text() or "0") if counter.exists() else 0
        counter.write_text(str(count + 1))
        return "ok"

    result = await gateway.invoke(context, ProviderPriority.RECOVERY, succeed)
    row = await storage.fetchone(
        "SELECT status,attempt,next_retry_at FROM provider_queue WHERE request_id=?",
        (REQUEST_ID,),
    )
    retry = await storage.fetchone(
        "SELECT attempt FROM provider_retry_state WHERE request_id=?",
        (REQUEST_ID,),
    )
    output.write_text(json.dumps({
        "result": result,
        "status": row[0],
        "attempt": row[1],
        "next_retry_at": row[2],
        "retry_attempt": retry[0] if retry else None,
    }))
    await gateway.stop()
    await storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["hold", "resume"])
    parser.add_argument("data_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.phase, args.data_root, args.output))
