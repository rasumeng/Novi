"""
WebUI Backend builder for Cozmo.

Uses CozmoContext to provide a single source of truth for models, memory,
and other services. Eliminates duplicate initialization across CLI,
WebUI, and other entry points.
"""

from .services.context import CozmoContext
from .runtime.runtime import _load_all_skills


class WebUIBackend:
    """Builds the shared backend for WebUI using CozmoContext."""

    def __init__(self, cfg: dict | None = None):
        import warnings
        warnings.filterwarnings("ignore")
        self._context = CozmoContext(cfg)
        self._context.warmup()

    def build_backend(self) -> dict:
        """Build the shared backend using CozmoContext services."""
        from .runtime.providers.mcp import MCPManager
        from .runtime.tool_registry import ToolRegistry
        from .orchestrator.intent import IntentDetector
        from .orchestrator.complexity import ComplexityEstimator
        from .orchestrator.evidence import EvidenceDetector
        from .orchestrator.orchestrator import Orchestrator

        ctx = self._context

        # Tool registry
        registry = ToolRegistry()
        from .tools import TOOL_REGISTRY
        for name, fn in TOOL_REGISTRY.items():
            registry.register(name, fn)

        # Capability registry
        from .capabilities import CapabilityRegistry
        from .capabilities.builtin import register_builtin_capabilities
        capability_registry = CapabilityRegistry()
        register_builtin_capabilities(capability_registry)

        # Model selection is workload-based and centralized: ModelService +
        # ModelSelector resolve llm.workloads.* at execution time. No router,
        # no default_model, no role config here.

        # MCP manager
        mcp = MCPManager(registry)
        mcp.start(ctx.config)

        # Telegram lifecycle seam (M5.3): the ``telegram.enabled`` setting
        # drives the bot through the ``integrations`` apply hook. The bot is
        # only built when the runtime explicitly requests it (enabled == true).
        from .services.telegram import TelegramLifecycle
        from .configuration.bootstrap import get_configuration as _get_configuration
        telegram = TelegramLifecycle(ctx)
        telegram.start(ctx.config)

        # M5.6: bind the Telegram tool to the lifecycle-owned runtime client.
        # The tool never reads a module-global bot; it resolves the ACTIVE
        # client (owned by the lifecycle) at call time so it tracks start/stop/
        # restart and fails safely while disabled.
        from .tools.telegram import make_telegram_send
        registry.register("telegram_send", make_telegram_send(telegram.get_runtime_client))

        # M5.4: Connector Registry — thin identity/status seam over the two
        # connectors. The registry describes them; MCPManager/TelegramLifecycle
        # keep owning lifecycle. Status is the connectors' own (safe) surface.
        from .connectors import ConnectorRegistry, ConnectorDefinition
        connectors = ConnectorRegistry()

        def _mcp_definition(source=None):
            mcp_cfg = (source or ctx.config).get("mcp", {}) or {}
            servers = mcp_cfg.get("servers", {}) or {}
            return dict(
                enabled=bool(mcp_cfg.get("enabled", True)),
                identity={"servers": sorted(str(s) for s in servers.keys())},
            )

        def _telegram_definition(source=None):
            tg = (source or ctx.config).get("telegram", {}) or {}
            return dict(enabled=bool(tg.get("enabled", False)))

        connectors.register(ConnectorDefinition(
            connector_id="mcp",
            connector_type="mcp",
            label="Model Context Protocol",
            status_fn=mcp.get_lifecycle,
            **_mcp_definition(),
        ))
        connectors.register(ConnectorDefinition(
            connector_id="telegram",
            connector_type="telegram",
            label="Telegram",
            status_fn=telegram.get_status,
            **_telegram_definition(),
        ))

        # M5.4: MCP server permission gate. Shared with every per-session
        # runtime so ``mcp.servers.<name>.permissions`` is actually consumed by
        # the existing ToolExecutor permission path. Config-derived, stateless.
        from .runtime.mcp_permissions import MCPPermissionGate
        mcp_permissions = MCPPermissionGate(ctx.config.get("mcp", {}) or {})

        # Shared skills
        skills = _load_all_skills()

        # Orchestrator components
        intent_detector = IntentDetector()
        complexity_estimator = ComplexityEstimator()
        evidence_detector = EvidenceDetector(llm=ctx.simple_llm)
        from .orchestrator.task_store import TaskStore
        from .planner.planner import PlannerEngine
        task_store = TaskStore()
        orchestrator = Orchestrator(
            intent_detector=intent_detector,
            complexity_estimator=complexity_estimator,
            evidence_detector=evidence_detector,
            capability_registry=capability_registry,
            task_store=task_store,
            planner_engine=PlannerEngine(),
        )
        job_manager = ctx.job_manager
        job_store = ctx.job_store

        # Continuation resolver — read-only join of TaskStore + JobStore.
        from .services.continuation import ContinuationService
        continuation = ContinuationService(
            task_store=task_store,
            job_store=job_store,
            job_manager=job_manager,
        )

        # Event-driven wiring (no polling): config changes reach the shared
        # backend live through the framework's apply hooks. ModelSelector wraps
        # ModelService, so refreshing discovery is all that's needed — selection
        # is re-read from llm.workloads.* at every resolution.
        def _reload_models(path, value, previous):
            if not path.startswith("llm."):
                return
            # A workload-model change is pure selection: it alters availability
            # not at all. Model resolution re-reads llm.workloads.* live and the
            # provider cache is keyed per model, so a changed selection builds a
            # fresh provider with no I/O here. Only other llm.* writes (e.g. the
            # Ollama URL) require a full provider re-list.
            if path.startswith("llm.workloads."):
                return
            try:
                ctx.model_service.refresh()
            except Exception as e:
                print(f"[cozmo] model refresh failed: {e}")

        def _safe_mcp_refresh(manager, path, value, previous):
            try:
                snap = _get_configuration().snapshot()
                manager.refresh_from_config(snap)
                mcp_permissions.refresh(snap.get("mcp", {}) or {})
                connectors.get("mcp").update(**_mcp_definition(snap))
            except Exception as e:
                print(f"[cozmo] MCP config refresh failed: {e}")

        def _safe_telegram_refresh(lifecycle, path, value, previous):
            try:
                snap = _get_configuration().snapshot()
                lifecycle.apply(snap)
                connectors.get("telegram").update(**_telegram_definition(snap))
            except Exception as e:
                print(f"[cozmo] Telegram config refresh failed: {e}")

        from .configuration.bootstrap import register_apply_hook as _ra
        _ra("runtime", _reload_models)
        _ra("mcp", lambda p, v, prev: _safe_mcp_refresh(mcp, p, v, prev))
        _ra("integrations", lambda p, v, prev: _safe_telegram_refresh(telegram, p, v, prev))

        return {
            "model_service": ctx.model_service,
            "simple_llm": ctx.simple_llm,
            "memory": ctx.memory,
            "project_index": ctx.project_index,
            "brain": ctx.brain,
            "registry": registry,
            "mcp": mcp,
            "telegram": telegram,
            "connectors": connectors,
            "mcp_permissions": mcp_permissions,
            "skills": skills,
            "capability_registry": capability_registry,
            "orchestrator": orchestrator,
            "job_manager": job_manager,
            "task_store": task_store,
            "job_store": job_store,
            "continuation": continuation,
            "context": ctx,
        }