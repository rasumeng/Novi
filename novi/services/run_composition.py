"""Application composition for the replacement run service."""

from __future__ import annotations

import inspect
import json
import threading
from pathlib import Path
from typing import Callable

from langchain_core.tools import StructuredTool

from novi.paths import home as app_home
from novi.runtime.agent_loop import AgentLoop
from novi.runtime.provider_adapter import LangChainTurnProvider
from novi.runtime.run_contracts import ModelSnapshot, RunState
from novi.services.permission_service import (
    AuthorizedToolDispatcher, PermissionService, ToolAuthorizationPolicy,
    ToolDescriptor,
)
from novi.services.run_service import RunService
from novi.services.run_store import RunStore
from novi.skills.catalog import SkillCatalog
from novi.skills.service import SkillService


_SYSTEM_PROMPT = """You are Novi. Use only native tool calls for actions.
Use report_progress for useful interim updates. Use activate_skill only when its
catalog description is relevant. A run completes naturally when you provide a
final response with no further tool calls. Never encode a tool call as JSON in
assistant prose."""

_RECOVERY_LOCK = threading.Lock()
_RECOVERED_DATABASES: set[str] = set()
_SOURCE_TOOL_NAMES = {"list_source_files", "search_source_files", "read_source_file"}


def build_run_service(ctx, *, persist_dir: str | Path | None = None,
                      headless: bool = False, auto: bool = False,
                      registry: dict[str, Callable] | None = None,
                      skill_root: str | Path | None = None) -> RunService:
    """Build the production replacement runtime from existing app services."""
    if registry is None:
        from novi.tools import TOOL_REGISTRY
        registry = dict(TOOL_REGISTRY)
    else:
        registry = dict(registry)

    descriptors = {name: _descriptor(name, fn) for name, fn in registry.items()}
    descriptors.update({name: ToolDescriptor(name=name, effects=("read",))
                        for name in _SOURCE_TOOL_NAMES})
    policy = ToolAuthorizationPolicy(
        tool_rules={name: "allow" for name in _SOURCE_TOOL_NAMES},
        global_rules=_permission_rules(ctx.config.get("permissions", {})),
        mode="bypass" if auto else "manual",
    )
    permissions = PermissionService(descriptors=descriptors, policy=policy)
    store = RunStore(persist_dir or (app_home() / "brain"))
    database_key = str(store.database_path.resolve())
    with _RECOVERY_LOCK:
        if database_key not in _RECOVERED_DATABASES:
            store.interrupt_active_runs()
            _RECOVERED_DATABASES.add(database_key)
    skills = SkillService(SkillCatalog(skill_root or (app_home() / "skills")))

    def loop_factory(state: RunState, cancelled):
        if state.request.model is not None:
            model_name = state.request.model.model
        else:
            _, model_name = ctx.model_service.resolve_primary()
        run_registry = dict(registry)
        source_prompt = ""
        try:
            workspace = ctx.workspace_service
            sources = workspace.get_conversation_sources(state.conversation_id)
        except Exception:
            workspace = None
            sources = []
        if workspace is not None and sources:
            conversation_id = state.conversation_id

            def list_source_files() -> str:
                """List files in the source folders attached to this conversation."""
                items = workspace.list_conversation_files(conversation_id)
                if not items:
                    return "No readable files were found in the attached source folders."
                return "\n".join(
                    f"[{item['source']}] {item['folder']}/{item['path']}"
                    for item in items
                )

            def search_source_files(query: str) -> str:
                """Search file names and text in the source folders attached to this conversation."""
                hits = workspace.search_conversation_sources(conversation_id, query)
                if not hits:
                    return "No matching files were found in the attached source folders."
                return json.dumps([{
                    "source": item.get("source_hash", ""),
                    "folder": Path(item.get("source_root", "")).name,
                    "path": item.get("path", ""),
                    "snippet": item.get("snippet", ""),
                } for item in hits], ensure_ascii=False)

            def read_source_file(source: str, path: str) -> str:
                """Read a text file from an attached source using its source id and relative path."""
                text = workspace.read_conversation_file(conversation_id, source, path)
                return text if text is not None else "Error: source file not found or not readable"

            run_registry.update({
                "list_source_files": list_source_files,
                "search_source_files": search_source_files,
                "read_source_file": read_source_file,
            })
            labels = ", ".join(Path(item["root"]).name or item["root"] for item in sources)
            source_prompt = (
                f"\nThis conversation has READ-only source folders attached: {labels}. "
                "When the user refers to an attached folder, inspect it with list_source_files, "
                "search_source_files, and read_source_file. Do not ask the user for its path."
            )

        tools = [_as_model_tool(name, fn) for name, fn in run_registry.items()]
        tools.extend(_control_tools())
        bound = ctx.model_service.bind_model(model_name, tools, temperature=0.0)
        executors = {name: _executor(fn) for name, fn in run_registry.items()}
        dispatcher = AuthorizedToolDispatcher(permission_service=permissions,
            exposed_tools=set(run_registry), run_id=state.id, executors=executors,
            headless=headless)
        catalog = skills.begin_run(state.id)
        catalog_text = "\n".join(
            f"- {item['name']}: {item['description']}" for item in catalog)
        prompt = _SYSTEM_PROMPT + source_prompt
        if catalog_text:
            prompt += "\nAvailable skills (activate explicitly before use):\n" + catalog_text
        return AgentLoop(LangChainTurnProvider(bound, system_prompt=prompt), dispatcher,
                         cancelled=cancelled, skill_service=skills)

    # Phase 8: finalized run evidence flows to Brain once, not per loop.
    brain = getattr(ctx, "brain", None)
    try:
        brain = ctx.brain if brain is None else brain
    except Exception:
        brain = None
    return RunService(store, loop_factory, permission_service=permissions, brain=brain,
                      inference=getattr(ctx.model_service, "inference", None))


def primary_model_snapshot(ctx) -> ModelSnapshot:
    provider, model = ctx.model_service.resolve_primary()
    # Snapshot known capability hints without triggering network discovery.
    supports: bool | None = None
    caps: tuple[str, ...] = ()
    try:
        reg = getattr(ctx.model_service, "_registry", None)
        if reg is not None:
            info = reg.find(model)
            if info is not None:
                tags = [t.lower() for t in getattr(info, "tags", [])]
                has_tools = any(t == "tools" for t in tags)
                if has_tools:
                    supports = True
                    caps = ("tools",)
                elif tags:
                    supports = False
                    caps = tuple(tags)
    except Exception:
        pass
    return ModelSnapshot(provider=provider, model=model, supports_tools=supports, capabilities=caps)


def _permission_rules(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(name): rule for name, rule in value.items()
            if isinstance(rule, str) and rule in {"allow", "ask", "deny"}}


def _descriptor(name: str, fn: Callable) -> ToolDescriptor:
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        parameters = {}
    path_arguments = tuple(key for key in parameters
                           if key in {"path", "file", "directory", "cwd", "root"})
    command_arguments = tuple(key for key in parameters
                              if key in {"command", "cmd", "script"})
    effect = "external" if name not in {"calculator", "read_file", "glob", "grep"} else "read"
    return ToolDescriptor(name=name, effects=(effect,),
                          path_arguments=path_arguments,
                          command_arguments=command_arguments)


def _executor(fn: Callable):
    def execute(arguments):
        value = fn(**arguments)
        text = getattr(value, "text", None)
        return text if isinstance(text, str) else value
    return execute


def _as_model_tool(name: str, fn: Callable) -> StructuredTool:
    description = (inspect.getdoc(fn) or f"Execute {name}.").splitlines()[0]
    return StructuredTool.from_function(func=fn, name=name, description=description)


def _control_tools() -> list[StructuredTool]:
    def report_progress(message: str) -> str:
        """Share a concise, useful progress update with the user."""
        return message

    def activate_skill(name: str) -> str:
        """Activate one available skill by its exact catalog name."""
        return name

    return [StructuredTool.from_function(fn) for fn in
            (report_progress, activate_skill)]
