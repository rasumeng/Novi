"""Application composition for the replacement run service."""

from __future__ import annotations

import inspect
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
catalog description is relevant. End every run by calling finish_task exactly
once with a truthful summary and outcome of completed or blocked. Never encode
a tool call as JSON in assistant prose."""

_RECOVERY_LOCK = threading.Lock()
_RECOVERED_DATABASES: set[str] = set()


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
    policy = ToolAuthorizationPolicy(
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
        _, configured_model = ctx.model_service.resolve_primary()
        model_name = state.request.model.model if state.request.model else configured_model
        tools = [_as_model_tool(name, fn) for name, fn in registry.items()]
        tools.extend(_control_tools())
        bound = ctx.model_service.bind_model(model_name, tools, temperature=0.0)
        executors = {name: _executor(fn) for name, fn in registry.items()}
        dispatcher = AuthorizedToolDispatcher(permission_service=permissions,
            exposed_tools=set(registry), run_id=state.id, executors=executors,
            headless=headless)
        catalog = skills.begin_run(state.id)
        catalog_text = "\n".join(
            f"- {item['name']}: {item['description']}" for item in catalog)
        prompt = _SYSTEM_PROMPT
        if catalog_text:
            prompt += "\nAvailable skills (activate explicitly before use):\n" + catalog_text
        return AgentLoop(LangChainTurnProvider(bound, system_prompt=prompt), dispatcher,
                         cancelled=cancelled, skill_service=skills)

    return RunService(store, loop_factory, permission_service=permissions)


def primary_model_snapshot(ctx) -> ModelSnapshot:
    provider, model = ctx.model_service.resolve_primary()
    return ModelSnapshot(provider=provider, model=model)


def _permission_rules(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(name): rule for name, rule in value.items()
            if rule in {"allow", "ask", "deny"}}


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

    def finish_task(summary: str, outcome: str) -> str:
        """Finish the run; outcome must be completed or blocked."""
        return summary

    return [StructuredTool.from_function(fn) for fn in
            (report_progress, activate_skill, finish_task)]
