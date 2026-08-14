"""Policy-enforced long-term memory service for standard v2 agents.

The LangGraph SQLite Store is the persistence primitive.  This module owns the
business policy around it: namespace access, status transitions, redaction,
source evidence and bounded hybrid retrieval.  Memory is context only; it
never acts as a TaskTree/receipt/acceptance authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from onemancompany.core.config import EMPLOYEES_DIR, EX_EMPLOYEES_DIR, PROJECTS_DIR
from onemancompany.core.runtime_context import get_task_runtime_context
from onemancompany.core.runtime_storage import RuntimeStorage, iso_now

MEMORY_TYPES = {"semantic", "episodic", "procedural"}
SCOPES = {"employee", "project", "company"}
ACTIVE_STATUSES = {"active", "verified"}
DEFAULT_LIMIT = 8
MAX_INJECTED_CHARS = 6000
DEFAULT_MODEL_INPUT_BUDGET_CHARS = 30000

_AUTHORIZATION_SECRET = re.compile(
    r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+"
)
_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_STYLE_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def redact_sensitive(text: str) -> str:
    """Remove common credentials without retaining any secret substring."""
    result = str(text or "")
    result = _AUTHORIZATION_SECRET.sub("Authorization=[REDACTED]", result)
    result = _KEY_VALUE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", result)
    result = _BEARER_SECRET.sub("Bearer [REDACTED]", result)
    return _OPENAI_STYLE_SECRET.sub("[REDACTED]", result)


def redact_sensitive_value(value: Any) -> Any:
    """Recursively redact strings before data reaches Store/checkpoint logs."""
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, dict):
        return {str(key): redact_sensitive_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_value(item) for item in value]
    return value


def _content_hash(memory_type: str, subject: str, text: str, structured_value: dict) -> str:
    raw = json.dumps(
        {"memory_type": memory_type, "subject": subject, "text": text, "structured_value": structured_value},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MemoryAccessError(PermissionError):
    pass


class MemoryService:
    """Long-term memory policy facade over ``RuntimeStorage.memory_store``."""

    def __init__(self, storage: RuntimeStorage) -> None:
        if storage.memory_store is None:
            raise RuntimeError("memory store is not initialized")
        self.storage = storage

    @staticmethod
    def context() -> dict[str, Any]:
        return get_task_runtime_context()

    @staticmethod
    def _base_project_id(project_id: str) -> str:
        """Normalize a project/iteration identifier to its shared project scope."""
        return str(project_id or "").split("/", 1)[0]

    @classmethod
    def project_members(cls, project_id: str) -> set[str]:
        project_id = cls._base_project_id(project_id)
        if not project_id:
            return set()
        path = Path(PROJECTS_DIR) / project_id / "project.yaml"
        if not path.exists():
            return set()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return set()
        members: set[str] = set()
        for member in data.get("team", []) or []:
            if isinstance(member, dict) and member.get("employee_id"):
                members.add(str(member["employee_id"]))
        return members

    def _allowed_namespaces(self, employee_id: str, project_id: str = "") -> list[tuple[str, ...]]:
        namespaces = [("employee", employee_id, kind) for kind in MEMORY_TYPES]
        canonical_project_id = self._base_project_id(project_id)
        if canonical_project_id and employee_id in self.project_members(canonical_project_id):
            namespaces.extend(("project", canonical_project_id, kind) for kind in MEMORY_TYPES)
        namespaces.extend(("company", kind) for kind in MEMORY_TYPES)
        return namespaces

    def _assert_namespace_access(self, namespace: tuple[str, ...], employee_id: str, project_id: str = "") -> None:
        if namespace not in self._allowed_namespaces(employee_id, project_id):
            raise MemoryAccessError("employee is not allowed to access this memory namespace")

    def _index_arg(self):
        return None if getattr(self.storage, "memory_vector_enabled", False) else False

    @staticmethod
    def _assert_employee_can_write(employee_id: str) -> None:
        """Reject writes from employees that only exist in the ex-employee archive."""
        if employee_id and (Path(EX_EMPLOYEES_DIR) / employee_id).exists() and not (Path(EMPLOYEES_DIR) / employee_id).exists():
            raise MemoryAccessError("former employees cannot create new private memory")

    async def _find_memory(self, memory_id_or_key: str) -> tuple[tuple[str, ...], str, dict[str, Any]]:
        for namespace in await self.storage.memory_store.alist_namespaces(limit=10000):
            rows = await self.storage.memory_store.asearch(namespace, query=None, limit=10000)
            for item in rows:
                value = dict(item.value or {})
                if item.key == memory_id_or_key or value.get("memory_id") == memory_id_or_key:
                    return tuple(item.namespace), item.key, value
        raise KeyError("memory not found")

    @staticmethod
    def _public_memory(value: dict[str, Any]) -> dict[str, Any]:
        """Return a recursively redacted copy for prompts and admin responses."""
        return dict(redact_sensitive_value(value))

    async def list_memories(self, *, status: str = "", scope: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for namespace in await self.storage.memory_store.alist_namespaces(limit=10000):
            if scope and namespace and namespace[0] != scope:
                continue
            for item in await self.storage.memory_store.asearch(namespace, query=None, limit=10000):
                value = dict(item.value or {})
                if status and value.get("status") != status:
                    continue
                value.update({"key": item.key, "namespace": list(item.namespace)})
                rows.append(self._public_memory(value))
        rows.sort(key=lambda value: str(value.get("created_at") or ""), reverse=True)
        return rows[max(0, offset):max(0, offset) + max(1, min(limit, 500))]

    async def get_memory(self, memory_id_or_key: str) -> dict[str, Any]:
        namespace, key, value = await self._find_memory(memory_id_or_key)
        return self._public_memory({"key": key, "namespace": list(namespace), **value})

    async def propose(
        self,
        *,
        employee_id: str,
        memory_type: str,
        subject: str,
        text: str,
        scope: str = "auto",
        project_id: str = "",
        structured_value: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        source_node_id: str = "",
        source_iteration_id: str = "",
        source_thread_id: str = "",
        confidence: float = 0.5,
        expires_at: str | None = None,
        dedupe_key: str | None = None,
        trusted_source: bool = False,
    ) -> dict[str, Any]:
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"unsupported memory_type: {memory_type}")
        if scope == "auto":
            scope = "project" if project_id else "employee"
        if scope not in SCOPES:
            raise ValueError(f"unsupported scope: {scope}")
        canonical_project_id = self._base_project_id(project_id)
        if scope == "company":
            namespace = ("company", memory_type)
        elif scope == "project":
            if not canonical_project_id:
                raise ValueError("project memory requires project_id")
            namespace = ("project", canonical_project_id, memory_type)
        else:
            namespace = ("employee", employee_id, memory_type)
        self._assert_namespace_access(namespace, employee_id, project_id)
        self._assert_employee_can_write(employee_id)
        if dedupe_key:
            existing = await self.storage.memory_store.aget(namespace, dedupe_key)
            if existing is not None:
                return {"key": existing.key, **dict(existing.value or {})}

        evidence = [
            redact_sensitive(str(item)).strip()
            for item in (evidence_refs or [])
            if str(item).strip()
        ]
        safe_text = redact_sensitive(text).strip()
        safe_subject = redact_sensitive(subject).strip()
        safe_structured = redact_sensitive_value(structured_value or {})
        safe_source_iteration_id = redact_sensitive(source_iteration_id).strip()
        safe_source_node_id = redact_sensitive(source_node_id).strip()
        safe_source_thread_id = redact_sensitive(source_thread_id).strip()
        # Model-only conclusions are never verified.  Evidence-backed project
        # facts are eligible for verified status; company memory always requires approval.
        status = "active" if scope == "employee" and memory_type == "episodic" else "candidate"
        if scope == "project" and trusted_source and evidence and safe_source_node_id:
            status = "verified"
        if scope == "company":
            status = "candidate"

        conflicting: tuple[str, dict[str, Any]] | None = None
        if scope == "project" and memory_type == "semantic" and status == "verified":
            for item in await self.storage.memory_store.asearch(namespace, query=None, limit=1000):
                previous = dict(item.value or {})
                if (
                    previous.get("status") == "verified"
                    and previous.get("subject") == safe_subject
                    and (previous.get("text") != safe_text or previous.get("structured_value") != safe_structured)
                ):
                    conflicting = (item.key, previous)
                    status = "candidate"
                    previous["status"] = "disputed"
                    previous["disputed_at"] = iso_now()
                    await self.storage.put_memory(
                        namespace, item.key, previous, index=self._index_arg()
                    )
                    break
        memory_id = str(uuid.uuid4())
        key = dedupe_key or f"{memory_id}:{_content_hash(memory_type, safe_subject, safe_text, safe_structured)[:16]}"
        value = {
            "memory_id": memory_id,
            "scope": scope,
            "namespace_id": employee_id if scope == "employee" else canonical_project_id if scope == "project" else "company",
            "memory_type": memory_type,
            "subject": safe_subject,
            "text": safe_text,
            "structured_value": safe_structured,
            "status": status,
            "created_by": employee_id or "system",
            "source_project_id": canonical_project_id,
            "source_iteration_id": safe_source_iteration_id,
            "source_node_id": safe_source_node_id,
            "source_thread_id": safe_source_thread_id,
            "dedupe_key": dedupe_key,
            "evidence_refs": evidence,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "valid_from": iso_now(),
            "expires_at": expires_at,
            "embedding_status": "pending" if not getattr(self.storage, "memory_vector_enabled", False) else "indexed",
            "embedding_index_version": getattr(self.storage, "memory_index_version", "v1"),
            "created_at": iso_now(),
            "verified_at": iso_now() if status in ACTIVE_STATUSES and status == "verified" else None,
        }
        await self.storage.put_memory(namespace, key, value, index=self._index_arg())
        if conflicting is not None:
            old_key, previous = conflicting
            conflict_id = str(uuid.uuid4())
            await self.storage.execute(
                "INSERT INTO memory_conflicts(conflict_id,namespace_json,fact_key,old_memory_key,new_memory_key,conflict_type,status,detected_at) VALUES (?,?,?,?,?,?,?,?)",
                (conflict_id, json.dumps(list(namespace)), safe_subject, old_key, key, "fact_value_changed", "open", iso_now()),
            )
            await self.storage.append_audit("memory_conflict_detected", {
                "conflict_id": conflict_id, "old_memory_id": previous.get("memory_id"), "new_memory_id": memory_id,
            })
            value["conflict_id"] = conflict_id
        return {"key": key, **value}

    async def search(
        self,
        *,
        employee_id: str,
        query: str,
        project_id: str = "",
        limit: int = DEFAULT_LIMIT,
        include_unverified: bool = False,
        max_chars: int = MAX_INJECTED_CHARS,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), DEFAULT_LIMIT))
        max_chars = max(0, min(int(max_chars), MAX_INJECTED_CHARS))
        results: list[dict[str, Any]] = []
        for namespace in self._allowed_namespaces(employee_id, project_id):
            vector_query = query or None
            if not getattr(self.storage, "memory_vector_enabled", False):
                vector_query = None
            # Apply trust status in SQL before vector limiting. Otherwise a large
            # set of similar candidates could crowd verified/active memories out
            # of the result window before policy filtering runs.
            filters = [None] if include_unverified else [
                {"status": status} for status in sorted(ACTIVE_STATUSES)
            ]
            for status_filter in filters:
                try:
                    rows = await self.storage.memory_store.asearch(
                        namespace,
                        query=vector_query,
                        filter=status_filter,
                        limit=limit * 2,
                    )
                except Exception:
                    rows = await self.storage.memory_store.asearch(
                        namespace,
                        query=None,
                        filter=status_filter,
                        limit=limit * 2,
                    )
                for row in rows:
                    value = dict(row.value or {})
                    status = str(value.get("status", "candidate"))
                    if not include_unverified and status not in ACTIVE_STATUSES:
                        continue
                    expires = _parse_time(value.get("expires_at"))
                    if expires and expires <= _now():
                        continue
                    value["key"] = row.key
                    value["namespace"] = list(row.namespace)
                    value["score"] = getattr(row, "score", None)
                    results.append(self._public_memory(value))
        # Deterministic structured fallback ranking: verified/source-backed facts
        # first, then semantic score, then recency.
        # Stable two-pass ordering keeps newest records first within the
        # stronger trust/semantic ranking without relying on platform-specific
        # negative timestamps for ``datetime.min``.
        results.sort(
            key=lambda value: _parse_time(value.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        results.sort(key=lambda value: (
            0 if value.get("status") == "verified" else 1,
            -(float(value.get("score") or 0.0)),
        ))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        chars = 0
        for value in results:
            memory_id = str(value.get("memory_id") or value.get("key"))
            if memory_id in seen:
                continue
            rendered = f"{value.get('memory_id')} {value.get('scope')} {value.get('status')} {value.get('source_node_id')} {value.get('text', '')}"
            if chars + len(rendered) > max_chars:
                continue
            seen.add(memory_id)
            deduped.append(value)
            chars += len(rendered)
            if len(deduped) >= limit:
                break
        return deduped

    async def _review(self, *, memory_id_or_key: str, decision: str, admin_id: str, notes: str = "", supersedes: str = "") -> dict[str, Any]:
        if not admin_id:
            raise MemoryAccessError("admin identity is required")
        namespace, key, value = await self._find_memory(memory_id_or_key)
        if decision == "approve":
            if value.get("status") not in {"candidate", "disputed"}:
                raise ValueError("only candidate or disputed memory can be approved")
            value["status"] = "verified"
            value["verified_at"] = iso_now()
            conflict = await self.storage.fetchone(
                "SELECT conflict_id,old_memory_key FROM memory_conflicts WHERE new_memory_key=? AND status='open' ORDER BY detected_at DESC LIMIT 1",
                (key,),
            )
            if conflict:
                old_namespace, old_key, old_value = await self._find_memory(str(conflict[1]))
                old_value["status"] = "superseded"
                old_value["superseded_at"] = iso_now()
                await self.storage.put_memory(
                    old_namespace, old_key, old_value, index=self._index_arg()
                )
                value["supersedes"] = old_value.get("memory_id")
                await self.storage.execute(
                    "UPDATE memory_conflicts SET status='resolved',resolution_action='supersede_old',resolved_by=?,resolved_at=? WHERE conflict_id=?",
                    (admin_id, iso_now(), conflict[0]),
                )
        elif decision == "reject":
            if value.get("status") not in {"candidate", "disputed"}:
                raise ValueError("only candidate or disputed memory can be rejected")
            value["status"] = "rejected"
            value["rejected_at"] = iso_now()
        elif decision == "supersede":
            if value.get("status") in {"superseded", "rejected"}:
                raise ValueError("rejected or superseded memory cannot be superseded again")
            if not supersedes:
                raise ValueError("superseded_by verified memory is required")
            target_namespace, target_key, target_value = await self._find_memory(supersedes)
            if target_key == key and target_namespace == namespace:
                raise ValueError("memory cannot supersede itself")
            if target_namespace != namespace:
                raise ValueError("superseding memory must use the same namespace")
            if target_value.get("status") != "verified":
                raise ValueError("superseding memory must be verified")
            if target_value.get("memory_type") != value.get("memory_type"):
                raise ValueError("superseding memory must use the same memory_type")
            if target_value.get("subject") != value.get("subject"):
                raise ValueError("superseding memory must use the same subject")
            value = self._public_memory(value)
            target_value = self._public_memory(target_value)
            value["status"] = "superseded"
            value["superseded_at"] = iso_now()
            value["superseded_by"] = target_value.get("memory_id") or target_key
            target_value["supersedes"] = value.get("memory_id") or key
            await self.storage.put_memory(
                target_namespace, target_key, target_value, index=self._index_arg()
            )
        else:
            raise ValueError("unsupported memory review decision")
        value = self._public_memory(value)
        safe_admin_id = redact_sensitive(admin_id)[:100]
        await self.storage.put_memory(namespace, key, value, index=self._index_arg())
        await self.storage.execute(
            "INSERT INTO memory_reviews(review_id,namespace_json,memory_key,decision,decided_by,notes,decided_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), json.dumps(list(namespace)), key, decision, safe_admin_id, redact_sensitive(notes), iso_now()),
        )
        audit_type = {"approve": "memory_approved", "reject": "memory_rejected", "supersede": "memory_superseded"}[decision]
        await self.storage.append_audit(audit_type, {
            "memory_id": value.get("memory_id"), "decided_by": safe_admin_id,
        })
        return self._public_memory({"key": key, "namespace": list(namespace), **value})

    async def approve(self, *, memory_id_or_key: str, admin_id: str, notes: str = "") -> dict[str, Any]:
        return await self._review(memory_id_or_key=memory_id_or_key, decision="approve", admin_id=admin_id, notes=notes)

    async def reject(self, *, memory_id_or_key: str, admin_id: str, notes: str = "") -> dict[str, Any]:
        return await self._review(memory_id_or_key=memory_id_or_key, decision="reject", admin_id=admin_id, notes=notes)

    async def supersede(self, *, memory_id_or_key: str, admin_id: str, superseded_by: str = "", notes: str = "") -> dict[str, Any]:
        return await self._review(memory_id_or_key=memory_id_or_key, decision="supersede", admin_id=admin_id, notes=notes, supersedes=superseded_by)

    async def approve_company(self, *, memory_key: str, admin_id: str, notes: str = "") -> dict[str, Any]:
        namespace, _, _ = await self._find_memory(memory_key)
        if not namespace or namespace[0] != "company":
            raise MemoryAccessError("company approval requires company memory")
        return await self.approve(memory_id_or_key=memory_key, admin_id=admin_id, notes=notes)
