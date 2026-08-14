"""Read-only classification of durable runtime reconciliation findings.

This module never mutates TaskTree files, checkpoints, recovery rows, or the
memory outbox.  It separates actionable formal-workflow findings from legacy
system-automation artifacts so operators do not replay or delete authoritative
state based on an aggregate health counter.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from onemancompany.core.runtime_storage import RuntimeStorage

_ACTIVE_RECOVERY_STATUSES = {"blocked", "conflict", "orphan"}
_CURRENT_SYSTEM_ADHOC_PREFIX = "omc:system:adhoc:"
_LEGACY_SYSTEM_AUTOMATION_PREFIX = "omc:_sys_automation_"


def is_system_adhoc_checkpoint_thread(thread_id: str) -> bool:
    """Return whether a checkpoint belongs to non-formal system/adhoc work."""
    value = str(thread_id or "")
    return value.startswith(_CURRENT_SYSTEM_ADHOC_PREFIX) or value.startswith(
        _LEGACY_SYSTEM_AUTOMATION_PREFIX
    )


def _project_kind(project_id: str) -> str:
    value = str(project_id or "")
    if value.startswith("_sys_automation_"):
        return "system_automation"
    if value.startswith("_sys_"):
        return "system_other"
    return "formal_project" if value else "missing_project"


def _safe_json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


async def runtime_reconciliation_health(storage: RuntimeStorage) -> dict[str, Any]:
    """Return bounded counters suitable for the public health endpoint."""
    recovery_row = await storage.fetchone(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN status='orphan' "
        "AND reason='checkpoint_without_tasktree_node' "
        "AND (checkpoint_thread_id LIKE 'omc:system:adhoc:%' "
        "OR checkpoint_thread_id LIKE 'omc:_sys_automation_%') "
        "THEN 1 ELSE 0 END) "
        "FROM recoveries WHERE status IN ('blocked','conflict','orphan')"
    )
    total = int(recovery_row[0] or 0) if recovery_row else 0
    legacy = int(recovery_row[1] or 0) if recovery_row else 0
    outbox_row = await storage.fetchone(
        "SELECT COUNT(*), MIN(created_at) FROM memory_outbox "
        "WHERE status IN ('pending','processing','holding')"
    )
    return {
        "memory_outbox_backlog": int(outbox_row[0] or 0) if outbox_row else 0,
        "oldest_memory_event_at": outbox_row[1] if outbox_row else None,
        "checkpoint_actionable": max(0, total - legacy),
        "checkpoint_findings_total": total,
        "checkpoint_legacy_system_orphans": legacy,
    }


async def runtime_reconciliation_summary(
    storage: RuntimeStorage,
    *,
    include_items: bool = False,
) -> dict[str, Any]:
    """Return a sanitized, read-only summary of recovery and outbox state."""
    recovery_rows = await storage.fetchall(
        "SELECT recovery_id,node_id,reason,checkpoint_thread_id,status,created_at,updated_at "
        "FROM recoveries WHERE status IN ('blocked','conflict','orphan') ORDER BY created_at"
    )
    actionable_recoveries: list[dict[str, Any]] = []
    legacy_system_orphans: list[dict[str, Any]] = []
    recovery_statuses: Counter[str] = Counter()
    for row in recovery_rows:
        status = str(row[4] or "")
        if status not in _ACTIVE_RECOVERY_STATUSES:
            continue
        recovery_statuses[status] += 1
        item = {
            "recovery_id": str(row[0]),
            "node_id": str(row[1]),
            "reason": str(row[2]),
            "checkpoint_thread_id": str(row[3]),
            "status": status,
            "created_at": row[5],
            "updated_at": row[6],
        }
        if (
            status == "orphan"
            and item["reason"] == "checkpoint_without_tasktree_node"
            and is_system_adhoc_checkpoint_thread(item["checkpoint_thread_id"])
        ):
            legacy_system_orphans.append(item)
        else:
            actionable_recoveries.append(item)

    outbox_rows = await storage.fetchall(
        "SELECT event_id,namespace_json,payload_json,status,attempt,next_retry_at,last_error,created_at "
        "FROM memory_outbox WHERE status IN ('pending','processing','holding') ORDER BY created_at"
    )
    outbox_statuses: Counter[str] = Counter()
    outbox_kinds: Counter[str] = Counter()
    outbox_items: list[dict[str, Any]] = []
    missing_evidence = 0
    malformed_payloads = 0
    attempted = 0
    for row in outbox_rows:
        payload = _safe_json_object(row[2])
        if not payload:
            malformed_payloads += 1
        status = str(row[3] or "")
        attempt = int(row[4] or 0)
        project_id = str(payload.get("project_id") or payload.get("source_project_id") or "")
        kind = _project_kind(project_id)
        evidence_refs = payload.get("evidence_refs")
        evidence_count = len(evidence_refs) if isinstance(evidence_refs, list) else 0
        if evidence_count == 0:
            missing_evidence += 1
        if attempt > 0:
            attempted += 1
        outbox_statuses[status] += 1
        outbox_kinds[kind] += 1
        outbox_items.append(
            {
                "event_id": str(row[0]),
                "status": status,
                "attempt": attempt,
                "next_retry_at": row[5],
                "has_last_error": bool(row[6]),
                "created_at": row[7],
                "scope": str(payload.get("scope") or ""),
                "memory_type": str(payload.get("memory_type") or ""),
                "employee_id": str(payload.get("employee_id") or ""),
                "project_kind": kind,
                "source_node_id": str(payload.get("source_node_id") or ""),
                "has_source_thread_id": bool(payload.get("source_thread_id")),
                "evidence_count": evidence_count,
            }
        )

    summary: dict[str, Any] = {
        "mode": "read_only",
        "checkpoint_findings_total": len(recovery_rows),
        "checkpoint_actionable": len(actionable_recoveries),
        "checkpoint_legacy_system_orphans": len(legacy_system_orphans),
        "checkpoint_by_status": dict(sorted(recovery_statuses.items())),
        "memory_outbox_backlog": len(outbox_rows),
        "memory_outbox_by_status": dict(sorted(outbox_statuses.items())),
        "memory_outbox_by_project_kind": dict(sorted(outbox_kinds.items())),
        "memory_outbox_unattempted": len(outbox_rows) - attempted,
        "memory_outbox_attempted": attempted,
        "memory_outbox_missing_evidence": missing_evidence,
        "memory_outbox_malformed_payloads": malformed_payloads,
        "oldest_memory_event_at": outbox_rows[0][7] if outbox_rows else None,
        "recommendations": [
            "Do not resume or replay legacy system-automation checkpoint threads.",
            "Keep outbox rows pending until memory is enabled and embedding/vector gates pass.",
            "Review evidence-free legacy episodic events before enabling the worker.",
        ],
    }
    if include_items:
        summary["actionable_recoveries"] = actionable_recoveries
        summary["legacy_system_orphans"] = legacy_system_orphans
        summary["memory_outbox_items"] = outbox_items
    return summary
