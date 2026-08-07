"""
CozmoContext — composition root / dependency container.

Phase 0: establishes shared initialization that was previously
duplicated across cli.py, webui_server.py, and telegram entry point.

Phase B: ModelService replaces ModelManager for CLI entry points.
WebUI still uses ModelManager directly (migrated in later phase).

Wires services together but contains NO business logic, routing,
or model selection behavior.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .. import config as cozmo_config

log = logging.getLogger("cozmo.context")


class CozmoContext:
    """Application composition root.

    Lazily initializes shared services on first access.
    All entry points (CLI, WebUI, Telegram) consume the same wiring.

    Usage:
        ctx = CozmoContext()
        runtime = ctx.create_runtime()
        runtime.run("hello")
    """

    def __init__(self, cfg: dict | None = None):
        self._cfg: dict | None = cfg
        self._ollama_url: str | None = None
        self._model_service: object | None = None
        self._router_llm: object | None = None
        self._memory: object | None = None
        self._project_index: object | None = None
        self._scheduler: object | None = None
        self._embedding: object | None = None
        self._reranker: object | None = None
        self._knowledge_inited: bool = False
        self._brain: object | None = None
        self._brain_event_bus: object | None = None

    # ── config ──────────────────────────────────────────────────────────

    @property
    def config(self) -> dict:
        if self._cfg is None:
            self._cfg = cozmo_config.load()
        return self._cfg

    # ── provider wiring (Phase B: ModelService replaces ModelManager) ───

    @property
    def ollama_url(self) -> str:
        return self.config.get("ollama", {}).get("url", "http://localhost:11434")

    @property
    def model_registry(self):
        from ..models import ModelRegistry

        if not hasattr(self, "_model_registry"):
            self._model_registry = ModelRegistry()
        return self._model_registry

    @property
    def model_service(self):
        from ..models import ModelService

        if self._model_service is None:
            svc = ModelService(self.config, self.model_registry)
            svc.refresh()
            self._model_service = svc
        return self._model_service

    @property
    def embedding_service(self):
        from .embedding import EmbeddingService

        if self._embedding is None:
            self._embedding = EmbeddingService(self.config)
        return self._embedding

    @property
    def reranker_service(self):
        from .embedding import RerankerService

        if self._reranker is None:
            self._reranker = RerankerService(self.config)
        return self._reranker

    @property
    def installed_models(self) -> list[str]:
        return [m.name for m in self.model_registry.list_all()]

    @property
    def router_llm(self):
        """Lightweight wrapper around ModelService for intent classification & summarization.

        Provides the simple `invoke(prompt) -> str` API that `classify_intent`
        and history compaction expect.
        """
        from ..runtime.runtime import _RouterLLM
        if self._router_llm is None:
            self._router_llm = _RouterLLM(self.model_service, "chat")
        return self._router_llm

    @property
    def memory(self):
        from ..memory.manager import MemoryManager, set_memory_manager

        if self._memory is None:
            mem_cfg = self.config.get("memory", {})
            self._memory = MemoryManager(
                self.router_llm,
                persist_dir=str(Path.home() / ".cozmo" / "memory"),
                embed_model=self.embedding_service,
                max_turns=int(mem_cfg.get("max_turns_before_summary", 5)),
                max_short_term_pairs=int(mem_cfg.get("max_short_term_pairs", 10)),
            )
            set_memory_manager(self._memory)
        return self._memory

    @property
    def project_index(self):
        from ..code_indexer import ProjectIndex

        if self._project_index is None:
            self._project_index = ProjectIndex(Path.cwd())
        return self._project_index

    @property
    def brain(self):
        """Brain facade — the only abstraction the rest of the system
        interacts with for knowledge (Architecture Rule #1).

        Wired lazily with the shared services; carries its own EventBus for
        Brain domain events. Phase C wires extraction + knowledge/scenario
        layers: observe() persists turns, extracts atomic knowledge, and
        maintains scenarios. MemoryManager.query merges the knowledge store so
        retrieval sees the new layer.
        """
        from ..brain.layers.knowledge import KnowledgeLayer
        from ..brain.layers.scenarios import ScenarioLayer
        from ..brain.reasoning.extraction import KnowledgeExtractor, Summarizer
        from ..brain.storage.conversation_store import ConversationStore
        from ..brain.storage.relationship_store import RelationshipStore
        from ..brain.storage.scenario_store import ScenarioStore
        from ..brain.storage.vector_store import VectorStore
        from ..memory.knowledge_index import get_knowledge_index
        from ..runtime.event_bus import EventBus

        if self._brain is None:
            from ..brain import Brain, set_brain

            persist_dir = Path.home() / ".cozmo" / "brain"
            knowledge_store = VectorStore(
                persist_dir=persist_dir, embed_model=self.embedding_service
            )
            scenario_store = ScenarioStore(persist_dir=persist_dir)
            self._brain_event_bus = EventBus()
            self._brain = Brain(
                memory=self.memory,
                project_index=self.project_index,
                knowledge_index=get_knowledge_index(),
                conversation_store=ConversationStore(persist_dir=persist_dir),
                event_bus=self._brain_event_bus,
                extractor=KnowledgeExtractor(summarizer=Summarizer(llm=self.router_llm.invoke)),
                knowledge_layer=KnowledgeLayer(knowledge_store),
                scenario_layer=ScenarioLayer(scenario_store),
                relationship_store=RelationshipStore(persist_dir=persist_dir),
                tiered_resolver=True,
            )
            set_brain(self._brain)
        return self._brain

    @property
    def brain_event_bus(self):
        """Read-only accessor to the Brain's own EventBus (Milestone 4 bridge).

        Returns a reference to the bus the Brain already emits into; never
        takes ownership, never mutates Brain state. External consumers (e.g.
        the WebUI timeline bridge) subscribe here as passive observers. Safe
        to call before the Brain is built — forces a lazy build if needed.
        """
        if self._brain_event_bus is None:
            _ = self.brain
        return self._brain_event_bus

    @property
    def scheduler(self):
        from ..scheduler import Scheduler
        from ..tools.scheduler_task import init_scheduler_tool

        if self._scheduler is None:
            self._scheduler = Scheduler()
            self._scheduler.on_trigger = lambda s: None
            self._scheduler.start()
            init_scheduler_tool(self._scheduler)
        return self._scheduler

    @property
    def orchestrator(self):
        from ..orchestrator.orchestrator import Orchestrator
        from ..orchestrator.intent import IntentDetector
        from ..orchestrator.complexity import ComplexityEstimator
        from ..orchestrator.evidence import EvidenceDetector
        from ..orchestrator.task_store import TaskStore
        from ..planner.planner import PlannerEngine

        if not hasattr(self, "_orchestrator"):
            self._orchestrator = None
        if self._orchestrator is None:
            self._orchestrator = Orchestrator(
                intent_detector=IntentDetector(router_llm=self.router_llm),
                complexity_estimator=ComplexityEstimator(),
                evidence_detector=EvidenceDetector(router_llm=self.router_llm),
                task_store=TaskStore(),
                planner_engine=PlannerEngine(),
            )
        return self._orchestrator

    # ── lifecycle ───────────────────────────────────────────────────────

    def init_knowledge_index(self):
        from ..memory.knowledge_index import init_knowledge_index

        if not self._knowledge_inited:
            init_knowledge_index(
                knowledge_dir=self.config.get("workspace", {}).get("knowledge", "~/.cozmo/knowledge"),
                persist_dir=str(Path.home() / ".cozmo" / "knowledge_index"),
                reranker=self.reranker_service,
            )
            self._knowledge_inited = True

    def create_runtime(self, **overrides) -> object:
        from ..runtime.runtime import CozmoRuntime
        from ..runtime.event_bus import EventBus
        from ..orchestrator.projection import TaskLifecycleProjection

        orchestrator = overrides.get("orchestrator", self.orchestrator)
        runtime = CozmoRuntime(
            model_service=overrides.get("model_service", self.model_service),
            model_manager=overrides.get("model_manager", None),
            memory=overrides.get("memory", self.memory),
            project_index=overrides.get("project_index", self.project_index),
            cfg=overrides.get("cfg", self.config),
            router_llm=overrides.get("router_llm", self.router_llm),
            event_bus=overrides.get("event_bus", EventBus()),
            brain=overrides.get("brain", self.brain),
            skills=overrides.get("skills", None),
            registry=overrides.get("registry", None),
            orchestrator=orchestrator,
        )

        # Wire Task lifecycle projection: runtime only emits events; this
        # projection transitions + persists the owning Task via the store the
        # orchestrator already holds.
        task_store = getattr(orchestrator, "task_store", None) if orchestrator else None
        TaskLifecycleProjection(task_store).subscribe(runtime.event_bus)
        return runtime

    def warmup(self):
        """Eagerly initialize all services. Called at startup."""
        _ = self.model_service
        _ = self.router_llm
        _ = self.memory
        _ = self.project_index
        _ = self.embedding_service
        self.init_knowledge_index()
        _ = self.brain
        _ = self.scheduler
        log.info("CozmoContext: all services initialized")
