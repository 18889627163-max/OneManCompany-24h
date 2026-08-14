"""Unified tool registry — single source of truth for all tools.

Every tool (base, gated, role-specific, asset) is registered here with
metadata that drives per-employee permission filtering.

All tool execution — whether from LangChain agents or Claude CLI via MCP —
flows through the same ``execute_tool()`` path which handles context setup
and permission checks.

Usage:
    from onemancompany.core.tool_registry import tool_registry, ToolMeta

    tool_registry.register(my_tool, ToolMeta(name="my_tool", category="base"))
    tools = tool_registry.get_proxied_tools_for("00010")
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from onemancompany.core.config import TOOL_YAML_FILENAME, open_utf





@dataclass
class ToolMeta:
    """Metadata attached to every registered tool."""

    name: str
    category: str  # "base" | "gated" | "role" | "asset"
    allowed_roles: list[str] | None = None
    allowed_users: list[str] | None = None
    source: str = "internal"  # "internal" | "asset"
    # Side-effecting tools participate in the formal-v2 durable invocation
    # ledger. Read-only tools must remain uncached so they cannot return stale data.
    side_effecting: bool = False


# Category → checker mapping lives inside ToolRegistry._is_allowed to keep
# the dispatch table close to the logic it governs.


class ToolRegistry:
    """Central registry for all LangChain tools with permission-based filtering."""

    def __init__(self) -> None:
        self._tools: dict[str, object] = {}  # name → tool instance
        self._meta: dict[str, ToolMeta] = {}  # name → metadata

    # ------------------------------------------------------------------
    # Registration & lookup
    # ------------------------------------------------------------------

    def register(self, tool: object, meta: ToolMeta) -> None:
        """Register a tool with its metadata. Overwrites if name already exists."""
        self._tools[meta.name] = tool
        self._meta[meta.name] = meta

    def get_tool(self, name: str) -> object | None:
        """Return a single tool by name, or None if not found."""
        return self._tools.get(name)

    def get_meta(self, name: str) -> ToolMeta | None:
        """Return metadata for a tool, or None if not found."""
        return self._meta.get(name)

    def all_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    # ------------------------------------------------------------------
    # Permission-based filtering
    # ------------------------------------------------------------------

    def get_tools_for(self, employee_id: str) -> list:
        """Return filtered list of tools an employee is authorized to use.

        Filtering rules by category:
        - base: always included
        - gated: included if tool name in employee's tool_permissions
        - role: included if employee's role in meta.allowed_roles
        - asset: included if allowed_users is None OR employee_id in allowed_users
        """
        from onemancompany.core.store import load_employee

        emp_data = load_employee(employee_id)
        if not emp_data:
            logger.warning("get_tools_for: employee %s not found", employee_id)
            return []

        result = []
        for name, tool in self._tools.items():
            meta = self._meta[name]
            if self._is_allowed(meta, emp_data, employee_id):
                result.append(tool)
        return result

    def get_all_tools_except_roles(self, exclude_roles: frozenset[str] | None = None) -> list:
        """Return all tools, bypassing role restrictions except for specific excluded roles.

        Used for EA chat where EA needs near-full access.
        """
        result = []
        for name, tool in self._tools.items():
            meta = self._meta[name]
            if meta.category == "role" and meta.allowed_roles and exclude_roles:
                if any(r in exclude_roles for r in meta.allowed_roles):
                    continue
            result.append(tool)
        return result

    @staticmethod
    def _is_allowed(meta: ToolMeta, emp_data: dict, employee_id: str) -> bool:
        """Check whether an employee is allowed to use a tool based on its category."""
        # EA has full access to all tools — privileged role
        if emp_data.get("role", "") == "EA":
            return True

        category = meta.category

        if category in ("base", "gated"):
            return True

        if category == "role":
            if meta.allowed_roles is None:
                return True
            return emp_data.get("role", "") in meta.allowed_roles

        if category == "asset":
            # Company-provided asset tools: available to all employees
            if meta.source != "talent":
                return True
            # Talent-brought tools: filter by allowed_users/allowed_roles
            if meta.allowed_users is None and meta.allowed_roles is None:
                return True
            if meta.allowed_users and employee_id in meta.allowed_users:
                return True
            if meta.allowed_roles and emp_data.get("role", "") in meta.allowed_roles:
                return True
            return False

        logger.warning("Unknown tool category %r for tool %s", category, meta.name)
        return False

    # ------------------------------------------------------------------
    # Asset tool loading
    # ------------------------------------------------------------------

    def load_asset_tools(self, tools_dir=None) -> None:
        """Scan company/assets/tools/ and register langchain_module tools.

        For each subdirectory with a tool.yaml where type == "langchain_module",
        imports the Python module and collects all BaseTool instances.

        Args:
            tools_dir: Override directory to scan (default: TOOLS_DIR from config).
        """
        if tools_dir is None:
            from onemancompany.core.config import TOOLS_DIR
            tools_dir = TOOLS_DIR

        if not tools_dir.exists():
            logger.debug("Asset tools directory does not exist: %s", tools_dir)
            return

        import importlib.util

        import yaml
        from langchain_core.tools import BaseTool

        for entry in sorted(tools_dir.iterdir()):
            if not entry.is_dir():
                continue

            tool_yaml_path = entry / TOOL_YAML_FILENAME
            if not tool_yaml_path.exists():
                continue

            with open_utf(tool_yaml_path) as f:
                tool_conf = yaml.safe_load(f) or {}

            # Only load Python-based tool modules
            if tool_conf.get("type") != "langchain_module":
                continue

            folder_name = entry.name
            py_file = entry / f"{folder_name}.py"
            if not py_file.is_file():
                logger.debug("No %s.py found in asset tool %s", folder_name, folder_name)
                continue

            # Import the module and collect BaseTool instances
            try:
                spec = importlib.util.spec_from_file_location(
                    f"asset_tool_{folder_name}", str(py_file)
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception as exc:
                logger.warning("Failed to import asset tool %s: %s", folder_name, exc)
                continue

            # Extract allowed_users and allowed_roles from tool.yaml
            allowed_users = tool_conf.get("allowed_users")
            allowed_roles = tool_conf.get("allowed_roles")
            # If key is present but value is empty list or null, treat as restricted-to-nobody
            # If key is absent, stays None (unrestricted)

            # Talent-brought tools have source_talent in tool.yaml
            source = "talent" if tool_conf.get("source_talent") else "asset"

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, BaseTool):
                    meta = ToolMeta(
                        name=attr.name,
                        category="asset",
                        allowed_users=allowed_users,
                        allowed_roles=allowed_roles,
                        source=source,
                    )
                    self.register(attr, meta)
                    logger.debug("Registered asset tool: {} (from {})", attr.name, folder_name)


    # ------------------------------------------------------------------
    # Proxied tools — unified execution path for all employee types
    # ------------------------------------------------------------------

    def get_proxied_tools_for(self, employee_id: str) -> list:
        """Return LangChain tools that route through execute_tool().

        Unlike get_tools_for() which returns direct tool instances,
        this returns wrapper tools that go through the unified execution
        path (same as MCP). This ensures consistent context setup
        for both LangChain agents and Claude CLI agents.
        """
        from langchain_core.tools import StructuredTool
        from pydantic import create_model

        direct_tools = self.get_tools_for(employee_id)
        proxied = []
        for tool in direct_tools:
            tool_name = tool.name

            # Check if employee_id is a business parameter (required, no default)
            # vs a caller-identity parameter (optional, has default).
            # Business params (e.g. dispatch_child target) must NOT be overwritten.
            schema = getattr(tool, "args_schema", None)
            _is_identity_param = False
            if schema and "employee_id" in schema.model_fields:
                field = schema.model_fields["employee_id"]
                _is_identity_param = not field.is_required()

            if _is_identity_param:
                # Caller-identity param: inject at system level, strip from schema
                async def _proxy(emp_id=employee_id, tname=tool_name, **kwargs):
                    kwargs["employee_id"] = emp_id
                    return await execute_tool(emp_id, tname, kwargs)

                fields = {
                    k: (v.annotation, v)
                    for k, v in schema.model_fields.items()
                    if k != "employee_id"
                }
                schema = create_model(schema.__name__, **fields)
            else:
                # No employee_id or it's a business param: pass through as-is
                async def _proxy(emp_id=employee_id, tname=tool_name, **kwargs):
                    return await execute_tool(emp_id, tname, kwargs)

            # Some OpenAI-compatible providers reject an empty Pydantic object
            # schema because LangChain emits no ``required`` key (or serializes
            # it as null in a later adapter).  Use an explicit JSON-schema dict
            # for zero-argument tools so the wire payload is unambiguously a
            # valid object with an empty required array.  Keep model schemas for
            # tools with arguments because callers and tests rely on their
            # normal Pydantic validation.
            if schema is not None and hasattr(schema, "model_fields") and not schema.model_fields:
                schema = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }

            wrapper = StructuredTool.from_function(
                coroutine=_proxy,
                name=tool.name,
                description=tool.description,
                args_schema=schema,
            )
            proxied.append(wrapper)
        return proxied


# Module-level singleton
tool_registry = ToolRegistry()


# ------------------------------------------------------------------
# Unified tool execution — single path for all tool calls
# ------------------------------------------------------------------


def _canonical_tool_fingerprint(tool_name: str, args: dict) -> str:
    payload = json.dumps(
        {"tool_name": tool_name, "args": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _business_idempotency_key(tool_name: str, args: dict, fingerprint: str) -> str:
    for field in ("idempotency_key", "task_key", "node_id", "project_id"):
        value = args.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return f"args:{fingerprint}"


def _formal_side_effect_context(tool_name: str, args: dict):
    """Return the ledger boundary for a formal-v2 side effect, if active."""
    meta = tool_registry.get_meta(tool_name)
    if meta is None or not meta.side_effecting:
        return None
    from onemancompany.core.runtime_context import get_task_runtime_context
    from onemancompany.core.runtime_storage import get_runtime_storage

    context = get_task_runtime_context()
    node_id = str(context.get("node_id") or "")
    thread_id = str(context.get("checkpoint_thread_id") or "")
    generation = int(context.get("execution_generation") or 0)
    storage = get_runtime_storage()
    if storage is None or not node_id or generation < 1 or not thread_id.startswith("omc:"):
        return None
    fingerprint = _canonical_tool_fingerprint(tool_name, args)
    business_key = _business_idempotency_key(tool_name, args, fingerprint)
    tool_call_id = str(
        args.get("tool_call_id")
        or args.get("_tool_call_id")
        or context.get("tool_call_id")
        or f"{thread_id}:{tool_name}:{business_key}"
    )
    return storage, context, node_id, generation, fingerprint, business_key, tool_call_id


def _mark_tool_reconciliation_holding(context: dict, *, reason: str, detail: str) -> None:
    """Persist a formal node hold before returning control to the model."""
    tree_path = str(context.get("tree_path") or "")
    node_id = str(context.get("node_id") or "")
    if not tree_path or not node_id or not Path(tree_path).exists():
        return
    try:
        from datetime import datetime
        from onemancompany.core.task_lifecycle import TaskPhase
        from onemancompany.core.task_tree import get_tree, get_tree_lock, register_tree

        tree = get_tree(tree_path)
        node = tree.get_node(node_id)
        if node is None:
            return
        node.set_status(TaskPhase.HOLDING)
        node.hold_reason = reason
        node.hold_started_at = datetime.now().astimezone().isoformat()
        node.checkpoint_status = "reconciliation_required"
        node.execution_checkpoint = {
            **dict(node.execution_checkpoint or {}),
            "phase": "tool_invocation_reconciliation_required",
            "reason": reason,
            "detail": detail[:1000],
        }
        register_tree(tree_path, tree)
        with get_tree_lock(tree_path):
            tree.save(Path(tree_path))
    except Exception as exc:  # pragma: no cover - best effort after primary failure
        logger.error("Failed to persist tool reconciliation hold for {}: {}", node_id, exc)


async def execute_tool(employee_id: str, tool_name: str, args: dict) -> dict:
    """Execute a tool with proper context setup.

    This is the single execution path for ALL tool calls, whether from
    LangChain agents (via proxied tools) or Claude CLI (via MCP HTTP bridge).
    """
    from onemancompany.core.vessel import (
        _current_vessel, _current_task_id, employee_manager,
    )

    fn = tool_registry.get_tool(tool_name)
    if not fn:
        return {"status": "error", "message": f"Tool '{tool_name}' not found"}

    # Context vars may already be set by vessel._execute_task for
    # company-hosted agents. For MCP calls they won't be set yet.
    # Only override if not already set.
    vessel_token = None
    existing_task = None
    ledger_boundary = None
    ledger_prepared = False
    try:
        existing_vessel = _current_vessel.get(None)
        if existing_vessel is None and employee_id:
            vessel = employee_manager.get_handle(employee_id)
            if vessel:
                vessel_token = _current_vessel.set(vessel)

        # task_id is set per-tool-call for MCP, per-task for LangChain
        # Don't override if already set by vessel
        existing_task = _current_task_id.get(None)
        if existing_task is None:
            # For MCP calls, task_id comes from args or env — handled by caller
            pass

        # Defense-in-depth: auto-fill employee_id for MCP/CLI callers.
        # (LangChain proxied tools inject it at the proxy layer and strip it
        # from the LLM schema, so this only fires for MCP HTTP bridge calls.)
        if employee_id and not args.get("employee_id"):
            args["employee_id"] = employee_id

        # Pre-tool hooks
        from onemancompany.core.skill_hooks import run_hooks, should_block, get_updated_input, HookEvent
        task_id = existing_task or ""
        pre_results = await run_hooks(
            employee_id, HookEvent.PRE_TOOL,
            tool_name=tool_name, tool_input=args, task_id=task_id,
        )
        blocked, block_reason = should_block(pre_results)
        if blocked:
            return {"status": "blocked", "message": f"Blocked by hook: {block_reason}"}
        args = get_updated_input(pre_results, args)

        # Reserve the durable side-effect boundary only after hooks have
        # produced the final arguments. A completed invocation is replayed;
        # prepared/failed invocations fail closed for reconciliation.
        ledger_boundary = _formal_side_effect_context(tool_name, args)
        if ledger_boundary is not None:
            storage, runtime_context, node_id, generation, fingerprint, business_key, tool_call_id = ledger_boundary
            invocation = await storage.prepare_tool_invocation(
                node_id=node_id,
                execution_generation=generation,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                business_idempotency_key=business_key,
                request_fingerprint=fingerprint,
            )
            if invocation.get("replayed"):
                norm = dict(invocation.get("result") or {})
            else:
                ledger_prepared = True
                norm = None
        else:
            norm = None

        if norm is None:
            # Call the tool
            if hasattr(fn, "ainvoke"):
                result = await fn.ainvoke(args)
            elif hasattr(fn, "invoke"):
                result = fn.invoke(args)
            elif inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = fn(**args)

            # Normalize result
            if isinstance(result, dict):
                norm = result
            elif isinstance(result, list):
                norm = {"result": result}
            else:
                norm = {"result": str(result)}

            if ledger_boundary is not None and ledger_prepared:
                from onemancompany.core.memory_service import redact_sensitive_value

                storage, runtime_context, node_id, generation, fingerprint, business_key, _ = ledger_boundary
                safe_result = dict(redact_sensitive_value(norm))
                result_reference = next(
                    (
                        str(norm[key])
                        for key in ("receipt_id", "node_id", "project_id", "id")
                        if norm.get(key) not in (None, "")
                    ),
                    None,
                )
                await storage.complete_tool_invocation(
                    node_id=node_id,
                    execution_generation=generation,
                    tool_name=tool_name,
                    business_idempotency_key=business_key,
                    request_fingerprint=fingerprint,
                    result=safe_result,
                    result_reference=result_reference,
                )

        # Post-tool hooks (awaited for observability, results don't affect return)
        await run_hooks(
            employee_id, HookEvent.POST_TOOL,
            tool_name=tool_name, tool_input=args, tool_output=norm, task_id=task_id,
        )

        return norm
    except Exception as e:
        from onemancompany.core.runtime_storage import (
            ToolInvocationConflict,
            ToolInvocationReconciliationRequired,
        )

        if ledger_boundary is not None:
            storage, runtime_context, node_id, generation, fingerprint, business_key, _ = ledger_boundary
            if ledger_prepared and not isinstance(
                e, (ToolInvocationConflict, ToolInvocationReconciliationRequired)
            ):
                try:
                    await storage.fail_tool_invocation(
                        node_id=node_id,
                        execution_generation=generation,
                        tool_name=tool_name,
                        business_idempotency_key=business_key,
                        request_fingerprint=fingerprint,
                        error=str(e),
                    )
                except Exception as ledger_error:  # pragma: no cover
                    logger.error("Failed to persist tool ledger failure: {}", ledger_error)
            if isinstance(e, ToolInvocationConflict):
                hold_reason = "tool_idempotency_conflict"
            else:
                hold_reason = "tool_invocation_reconciliation_required"
            _mark_tool_reconciliation_holding(
                runtime_context,
                reason=hold_reason,
                detail=str(e),
            )

        logger.error("Tool '{}' failed: {}", tool_name, e)
        # Post-tool-failure hooks
        try:
            from onemancompany.core.skill_hooks import run_hooks, HookEvent
            await run_hooks(
                employee_id, HookEvent.POST_TOOL_FAILURE,
                tool_name=tool_name, tool_input=args, error_message=str(e),
                task_id=existing_task or "",
            )
        except Exception as hook_err:
            logger.debug("Post-tool-failure hook error (suppressed): {}", hook_err)
        if ledger_boundary is not None:
            return {"status": "holding", "message": str(e)}
        return {"status": "error", "message": str(e)}
    finally:
        if vessel_token is not None:
            _current_vessel.reset(vessel_token)
