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
        from .jobs.manager import JobManager

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
        default_model = ctx.config.get("llm", {}).get("default_model") or "qwen3:8b"
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
        orchestrator = Orchestrator(
            intent_detector=intent_detector,
            complexity_estimator=complexity_estimator,
            evidence_detector=evidence_detector,
            capability_registry=capability_registry,
            model_router=model_router,
        )
        job_manager = JobManager()

        return {
            "model_service": ctx.model_service,
            "router_llm": ctx.router_llm,
            "memory": ctx.memory,
            "project_index": ctx.project_index,
            "registry": registry,
            "mcp": mcp,
            "skills": skills,
            "capability_registry": capability_registry,
            "model_router": model_router,
            "orchestrator": orchestrator,
            "job_manager": job_manager,
            "context": ctx,
        }