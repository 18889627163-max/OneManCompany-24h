"""Agent-facing long-term memory tools with server-derived identity."""
from __future__ import annotations

from langchain_core.tools import StructuredTool

from onemancompany.core.memory_service import MemoryService, MemoryAccessError
from onemancompany.core.runtime_context import get_task_runtime_context
from onemancompany.core.runtime_storage import get_runtime_storage


def _service() -> MemoryService:
    from onemancompany.core.config import settings
    if not settings.omc_memory_enabled:
        raise RuntimeError("long-term memory is disabled")
    storage = get_runtime_storage()
    if storage is None or storage.memory_store is None:
        raise RuntimeError("memory backend unavailable")
    return MemoryService(storage)


async def _search_memory(query: str, limit: int = 8) -> dict:
    context = get_task_runtime_context()
    employee_id = str(context.get("employee_id") or "")
    if not employee_id:
        return {"status": "error", "message": "memory search requires formal employee identity"}
    try:
        rows = await _service().search(
            employee_id=employee_id,
            project_id=str(context.get("project_id") or ""),
            query=query,
            limit=limit,
        )
        return {"status": "ok", "count": len(rows), "memories": rows}
    except (MemoryAccessError, RuntimeError) as exc:
        return {"status": "error", "message": str(exc)}


async def _propose_memory(memory_type: str, subject: str, text: str, evidence_refs: list[str] | None = None) -> dict:
    context = get_task_runtime_context()
    employee_id = str(context.get("employee_id") or "")
    if not employee_id:
        return {"status": "error", "message": "memory proposal requires formal employee identity"}
    try:
        value = await _service().propose(
            employee_id=employee_id,
            memory_type=memory_type,
            subject=subject,
            text=text,
            project_id=str(context.get("project_id") or ""),
            evidence_refs=evidence_refs or [],
            source_node_id=str(context.get("node_id") or ""),
            source_iteration_id=str(context.get("iteration_id") or ""),
            source_thread_id=str(context.get("checkpoint_thread_id") or ""),
        )
        return {"status": "ok", "memory": value}
    except (MemoryAccessError, RuntimeError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}


search_memory = StructuredTool.from_function(
    coroutine=_search_memory,
    name="search_memory",
    description="Search permitted employee, project, and verified company long-term memory. Identity and namespaces are server-controlled.",
)
propose_memory = StructuredTool.from_function(
    coroutine=_propose_memory,
    name="propose_memory",
    description="Propose a candidate memory from the current task. You cannot mark memory verified or alter task state.",
)
MEMORY_TOOLS = [search_memory, propose_memory]
