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

        # Model router
        from .runtime.model_router import ModelRouter
        default_model = ctx.config.get("llm", {}).get("default_model") or ""
        model_router = ModelRouter(default_model=default_model, resource_manager=None)

        # MCP manager
        mcp = MCPManager(registry)
        mcp.start(ctx.config)

        # Shared skills
        skills = _load_all_skills()

        # Orchestrator components
        intent_detector = IntentDetector()
        complexity_estimator = ComplexityEstimator()
        evidence_detector = EvidenceDetector(router_llm=ctx.router_llm)
        from .orchestrator.task_store import TaskStore
        from .planner.planner import PlannerEngine
        task_store = TaskStore()
        orchestrator = Orchestrator(
            intent_detector=intent_detector,
            complexity_estimator=complexity_estimator,
            evidence_detector=evidence_detector,
            capability_registry=capability_registry,
            model_router=model_router,
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
        # backend live through the framework's apply hooks.
        def _reload_router(path, value, previous):
            if not path.startswith("llm."):
                return
            try:
                ctx.model_service.refresh()
            except Exception as e:
                print(f"[cozmo] model refresh failed: {e}")
            model_router.populate_from_service(ctx.model_service, ctx.config)

        def _safe_mcp_refresh(manager, path, value, previous):
            try:
                manager.refresh_from_config(ctx.config)
            except Exception as e:
                print(f"[cozmo] MCP config refresh failed: {e}")

        from .configuration.bootstrap import register_apply_hook as _ra
        _ra("runtime", _reload_router)
        _ra("mcp", lambda p, v, prev: _safe_mcp_refresh(mcp, p, v, prev))

        return {
            "model_service": ctx.model_service,
            "router_llm": ctx.router_llm,
            "memory": ctx.memory,
            "project_index": ctx.project_index,
            "brain": ctx.brain,
            "registry": registry,
            "mcp": mcp,
            "skills": skills,
            "capability_registry": capability_registry,
            "model_router": model_router,
            "orchestrator": orchestrator,
            "job_manager": job_manager,
            "task_store": task_store,
            "job_store": job_store,
            "continuation": continuation,
            "context": ctx,
        }