"""Per-task runtime context shared by execution, model and LangGraph layers.

The values live in :mod:`contextvars`, so concurrent employee tasks do not leak
checkpoint IDs, lease metadata, or provider callbacks into one another.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Any

# Conversation-scoped context retained for compatibility with conversation adapters.
_interaction_type: ContextVar[str] = ContextVar("_interaction_type", default="")
_interaction_work_dir: ContextVar[str] = ContextVar("_interaction_work_dir", default="")


def get_interaction_type() -> str:
    """Return the active conversation interaction type, if any."""
    return _interaction_type.get()


def get_interaction_work_dir() -> str:
    """Return the active conversation working directory, if any."""
    return _interaction_work_dir.get()


_task_runtime_context: ContextVar[dict[str, Any]] = ContextVar(
    "omc_task_runtime_context", default={}
)


def get_task_runtime_context() -> dict[str, Any]:
    """Return a defensive copy of the current task runtime context."""
    return dict(_task_runtime_context.get({}))


def set_task_runtime_context(context: dict[str, Any]) -> Token:
    """Set context for the current async task and return its reset token."""
    return _task_runtime_context.set(dict(context))


def reset_task_runtime_context(token: Token) -> None:
    _task_runtime_context.reset(token)


def langgraph_invoke_config(*, recursion_limit: int | None = None) -> dict[str, Any]:
    """Build a LangGraph config with a durable thread ID when storage is active.

    Formal task execution supplies the exact ``omc:<project>:<iteration>:<node>:gN``
    ID through the context. Calls outside a TaskTree use an isolated ad-hoc ID so
    enabling the official SQLite checkpointer never makes conversational calls
    fail due to a missing ``thread_id``.
    """
    config: dict[str, Any] = {}
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit

    from onemancompany.core.runtime_storage import get_runtime_storage

    if get_runtime_storage() is not None:
        context = get_task_runtime_context()
        thread_id = str(context.get("checkpoint_thread_id") or "")
        if not thread_id:
            thread_id = f"omc:system:adhoc:{uuid.uuid4().hex}:g1"
        config["configurable"] = {"thread_id": thread_id}
    return config
