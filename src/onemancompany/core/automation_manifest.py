"""Durable registration and execution of the standard 24h automation manifest.

The employee ``automations.yaml`` files remain a legacy/manual API.  The
versioned manifest in ``docs/automation/cron-tasks.yaml`` is the source for the
standard-v2 scheduled jobs.  Registration is persisted in RuntimeStorage so a
restart cannot silently forget which jobs were enabled.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.runtime_storage import RuntimeStorage, iso_now

_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "docs" / "automation" / "cron-tasks.yaml"
_CRON_PARTS = 5
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
_ALLOWED_FIELDS = {
    "*", "*/1", "*/2", "*/3", "*/4", "*/5", "*/6", "*/10", "*/12", "*/15", "*/20", "*/30",
}


def _cron_field_valid(value: str, minimum: int, maximum: int) -> bool:
    if value == "*":
        return True
    for part in value.split(","):
        if part == "*":
            continue
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                return False
            return step > 0 and step <= maximum - minimum + 1
        if "-" in part:
            bits = part.split("-", 1)
            try:
                lo, hi = int(bits[0]), int(bits[1])
            except ValueError:
                return False
            if not minimum <= lo <= hi <= maximum:
                return False
            continue
        try:
            number = int(part)
        except ValueError:
            return False
        if not minimum <= number <= maximum:
            return False
    return True


def validate_schedule(schedule: str) -> str:
    fields = str(schedule or "").split()
    if len(fields) != _CRON_PARTS:
        raise ValueError("schedule must use five cron fields")
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    if not all(_cron_field_valid(field, *limits) for field, limits in zip(fields, ranges)):
        raise ValueError(f"invalid cron schedule: {schedule}")
    return " ".join(fields)


def load_manifest(path: str | Path = _MANIFEST_PATH) -> list[dict[str, Any]]:
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    tasks = data.get("cron_tasks")
    if not isinstance(tasks, list):
        raise ValueError("cron_tasks must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in tasks:
        if not isinstance(raw, dict):
            raise ValueError("each cron task must be a mapping")
        task = dict(raw)
        task_id = str(task.get("id") or "")
        employee_id = str(task.get("employee_id") or "")
        if not _SAFE_ID.fullmatch(task_id):
            raise ValueError(f"invalid automation id: {task_id}")
        if task_id in seen:
            raise ValueError(f"duplicate automation id: {task_id}")
        seen.add(task_id)
        if not (EMPLOYEES_DIR / employee_id / "profile.yaml").exists():
            raise ValueError(f"automation {task_id} references missing employee {employee_id}")
        schedule = validate_schedule(str(task.get("schedule") or ""))
        key_template = str(task.get("task_key_template") or "")
        prompt = str(task.get("prompt_template") or "").strip()
        if not key_template or not prompt:
            raise ValueError(f"automation {task_id} requires task_key_template and prompt_template")
        task["id"] = task_id
        task["employee_id"] = employee_id
        task["schedule"] = schedule
        task["enabled"] = bool(task.get("enabled", True))
        task["priority"] = int(task.get("priority", 1))
        result.append(task)
    return result


async def ensure_registry_table(storage: RuntimeStorage) -> None:
    """Create and migrate the durable automation registry.

    The registry is deliberately independent from the in-memory scheduler.  A
    process crash can therefore be reconciled from the last run receipt rather
    than creating a second task on the next tick.
    """
    await storage.execute(
        """CREATE TABLE IF NOT EXISTS automation_registry (
            automation_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL,
            schedule TEXT NOT NULL,
            task_key_template TEXT NOT NULL,
            name TEXT NOT NULL,
            prompt_template TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            priority INTEGER NOT NULL,
            manifest_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            last_scheduled_at TEXT,
            last_dispatched_node_id TEXT,
            registration_receipt_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    # Databases created by the first implementation do not have the run
    # receipt/error columns.  ALTER TABLE is idempotent when guarded by
    # PRAGMA table_info, and keeps existing automation registrations intact.
    columns = {str(row[1]) for row in await storage.fetchall("PRAGMA table_info(automation_registry)")}
    for name, definition in {
        "last_dispatch_receipt_json": "TEXT",
        "last_error": "TEXT",
    }.items():
        if name not in columns:
            await storage.execute(f"ALTER TABLE automation_registry ADD COLUMN {name} {definition}")
    await storage.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_registry_status ON automation_registry(status, enabled)"
    )


async def register_manifest(storage: RuntimeStorage, path: str | Path = _MANIFEST_PATH) -> dict[str, Any]:
    tasks = load_manifest(path)
    await ensure_registry_table(storage)
    manifest_bytes = Path(path).read_bytes()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    registered = 0
    unchanged = 0
    now = iso_now()
    for task in tasks:
        receipt = {
            "receipt_type": "automation_registration",
            "automation_id": task["id"],
            "manifest_hash": f"sha256:{manifest_hash}",
            "registered_at": now,
            "source": str(Path(path).name),
        }
        row = await storage.fetchone(
            "SELECT manifest_hash,last_scheduled_at,last_dispatched_node_id,created_at FROM automation_registry WHERE automation_id=?",
            (task["id"],),
        )
        if row and row[0] == manifest_hash:
            unchanged += 1
            continue
        await storage.execute(
            """INSERT INTO automation_registry(
                automation_id,employee_id,schedule,task_key_template,name,prompt_template,
                enabled,priority,manifest_hash,status,last_scheduled_at,last_dispatched_node_id,
                registration_receipt_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(automation_id) DO UPDATE SET
                employee_id=excluded.employee_id,schedule=excluded.schedule,
                task_key_template=excluded.task_key_template,name=excluded.name,
                prompt_template=excluded.prompt_template,enabled=excluded.enabled,
                priority=excluded.priority,manifest_hash=excluded.manifest_hash,
                status=excluded.status,registration_receipt_json=excluded.registration_receipt_json,
                updated_at=excluded.updated_at""",
            (task["id"], task["employee_id"], task["schedule"], task["task_key_template"],
             str(task.get("name") or task["id"]), task["prompt_template"],
             int(task["enabled"]), task["priority"], manifest_hash,
             "registered" if task["enabled"] else "disabled",
             row[1] if row else None, row[2] if row else None,
             json.dumps(receipt, ensure_ascii=False, sort_keys=True), row[3] if row else now, now),
        )
        await storage.append_audit("automation_registered", receipt)
        registered += 1
    return {"manifest": str(Path(path).name), "manifest_hash": f"sha256:{manifest_hash}", "registered": registered, "unchanged": unchanged, "total": len(tasks)}


def _field_matches(field: str, value: int, *, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if part.startswith("*/"):
            return (value - minimum) % int(part[2:]) == 0
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            if lo <= value <= hi:
                return True
        elif int(part) == value:
            return True
    return False


def cron_matches(schedule: str, when: datetime) -> bool:
    minute, hour, day, month, weekday = schedule.split()
    # Python Monday=0; cron Sunday may be 0 or 7.
    cron_weekday = (when.weekday() + 1) % 7
    return (
        _field_matches(minute, when.minute, minimum=0, maximum=59)
        and _field_matches(hour, when.hour, minimum=0, maximum=23)
        and _field_matches(day, when.day, minimum=1, maximum=31)
        and _field_matches(month, when.month, minimum=1, maximum=12)
        and (weekday == "*" or _field_matches(weekday, cron_weekday, minimum=0, maximum=7))
    )


def render_task(task: dict[str, Any], when: datetime) -> tuple[str, str]:
    values = {"now": when.isoformat(), "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M:%S"), "datetime": when.isoformat(), "scheduled_at": when.isoformat(), "time_strategy": "保守"}
    prompt = str(task["prompt_template"]).format_map(values)
    key = str(task["task_key_template"]).format_map(values)
    return key, prompt


class ManifestAutomationRunner:
    def __init__(self, storage: RuntimeStorage, *, interval_seconds: float = 20.0) -> None:
        self.storage = storage
        self.interval_seconds = max(5.0, interval_seconds)
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        await ensure_registry_table(self.storage)
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="omc-manifest-automation")

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task:
            task.cancel()
            try:
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                logger.debug("Manifest automation task cancelled during shutdown")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Manifest automation tick failed: {}", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                logger.trace("Manifest automation interval elapsed")

    async def tick(self, when: datetime | None = None) -> int:
        when = when or datetime.now().astimezone().replace(second=0, microsecond=0)
        rows = await self.storage.fetchall(
            "SELECT automation_id,employee_id,schedule,task_key_template,prompt_template,last_scheduled_at,status "
            "FROM automation_registry WHERE enabled=1 AND status IN ('registered','holding')"
        )
        dispatched = 0
        for row in rows:
            task = dict(zip(("id", "employee_id", "schedule", "task_key_template", "prompt_template", "last_scheduled_at", "status"), row))
            if not cron_matches(task["schedule"], when):
                continue
            scheduled = when.isoformat()
            if task["last_scheduled_at"] == scheduled:
                continue
            task_key, prompt = render_task(task, when)
            try:
                node_id, tree_path = await self._dispatch_once(task, task_key, prompt, scheduled)
                receipt = {
                    "receipt_type": "automation_dispatch",
                    "automation_id": task["id"],
                    "parent_id": f"automation:{task['id']}",
                    "employee_id": task["employee_id"],
                    "task_key": task_key,
                    "node_id": node_id,
                    "tree_path": tree_path,
                    "scheduled_at": scheduled,
                }
                await self.storage.execute(
                    "UPDATE automation_registry SET status='registered',last_scheduled_at=?,"
                    "last_dispatched_node_id=?,last_dispatch_receipt_json=?,last_error=NULL,updated_at=? "
                    "WHERE automation_id=?",
                    (scheduled, node_id, json.dumps(receipt, ensure_ascii=False, sort_keys=True), iso_now(), task["id"]),
                )
                await self.storage.append_audit("automation_dispatch", receipt)
                dispatched += 1
            except Exception as exc:
                # A missing executor/runtime is an infrastructure wait.  Keep
                # the manifest row recoverable and never turn the business task
                # into a failed result.
                await self.storage.execute(
                    "UPDATE automation_registry SET status='holding',last_error=?,updated_at=? WHERE automation_id=?",
                    (f"{type(exc).__name__}: {str(exc)[:400]}", iso_now(), task["id"]),
                )
                await self.storage.append_audit("automation_holding", {
                    "automation_id": task["id"], "employee_id": task["employee_id"],
                    "task_key": task_key, "reason": type(exc).__name__,
                })
                logger.warning("Automation {} held: {}", task["id"], type(exc).__name__)
        return dispatched

    async def _dispatch_once(
        self, task: dict[str, Any], task_key: str, prompt: str, scheduled: str
    ) -> tuple[str, str]:
        """Prepare, create/reconcile, and durably complete one automation dispatch."""
        from onemancompany.api.routes import _push_adhoc_task
        from onemancompany.core.task_tree import get_tree
        from onemancompany.core.store import load_task_index

        automation_id = str(task["id"])
        employee_id = str(task["employee_id"])
        parent_id = f"automation:{automation_id}"
        fingerprint = hashlib.sha256(
            json.dumps({"employee_id": employee_id, "task_key": task_key, "prompt": prompt},
                       ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        intent = await self.storage.prepare_dispatch_intent(
            parent_id=parent_id, employee_id=employee_id,
            task_key=task_key, request_fingerprint=fingerprint,
        )

        # Recover a crash after filesystem scheduling but before the intent was
        # advanced.  The stable task_key makes this scan deterministic.
        node_id = intent.get("node_id")
        tree_path = ""
        for entry in load_task_index(employee_id):
            candidate_path = str(entry.get("tree_path") or "")
            candidate_id = str(entry.get("node_id") or "")
            if not candidate_path or not candidate_id:
                continue
            try:
                candidate = get_tree(candidate_path).get_node(candidate_id)
            except Exception as exc:
                logger.debug("Skipping unreadable task index entry {}: {}", candidate_path, type(exc).__name__)
                continue
            if candidate and candidate.task_key == task_key:
                node_id, tree_path = candidate_id, candidate_path
                break

        if node_id and not tree_path:
            raise RuntimeError("dispatch intent references a node missing from the employee task index")
        if not node_id:
            node_id, tree_path = _push_adhoc_task(
                employee_id, f"[automation:{automation_id}] {prompt}",
                project_id=f"_sys_automation_{automation_id}",
                task_key=task_key,
                request_fingerprint=fingerprint,
            )

        # Bind the durable receipt only after the TaskTree and schedule index
        # exist.  Replaying the same key returns the same node.
        receipt = {"receipt_type": "automation_dispatch",
                   "automation_id": automation_id, "task_key": task_key,
                   "node_id": node_id, "tree_path": tree_path, "scheduled_at": scheduled}
        await self.storage.advance_dispatch_intent(
            parent_id=parent_id, employee_id=employee_id, task_key=task_key,
            request_fingerprint=fingerprint, state="scheduled", node_id=node_id,
            receipt=receipt,
        )
        tree = get_tree(tree_path)
        node = tree.get_node(str(node_id))
        if node is None:
            raise RuntimeError("scheduled automation node is missing from TaskTree")
        node.dispatch_verification = {
            "verified": True,
            "receipt_type": "automation_dispatch",
            "parent_id": parent_id,
            **receipt,
        }
        tree.save(Path(tree_path))
        return str(node_id), str(tree_path)


_runner: ManifestAutomationRunner | None = None


async def start_manifest_automations(storage: RuntimeStorage) -> dict[str, Any]:
    global _runner
    registration = await register_manifest(storage)
    _runner = ManifestAutomationRunner(storage)
    await _runner.start()
    return registration


async def stop_manifest_automations() -> None:
    global _runner
    if _runner:
        await _runner.stop()
    _runner = None
