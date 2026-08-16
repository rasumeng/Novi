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
        self._simple_llm: object | None = None
        self._memory: object | None = None
        self._project_index: object | None = None
        self._scheduler: object | None = None
        self._embedding: object | None = None
        self._reranker: object | None = None
        self._knowledge_inited: bool = False
        self._brain: object | None = None
        self._brain_event_bus: object | None = None
        self._job_store: object | None = None
        self._job_manager: object | None = None
        self._job_lifecycle: object | None = None
        self._continuation: object | None = None
        self._recovered: bool = False

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
    def simple_llm(self):
        """Lightweight wrapper around ModelService for intent classification & summarization.

        Provides the simple `invoke(prompt) -> str` API that `classify_intent`
        and history compaction expect. Resolves the ``general`` workload's
        configured model — the advisory LLM surface, not a router.
        """
        from .simple_llm import SimpleLLM
        if self._simple_llm is None:
            self._simple_llm = SimpleLLM(self.model_service, "general")
        return self._simple_llm

    @property
    def memory(self):
        from ..memory.manager import MemoryManager, set_memory_manager

        if self._memory is None:
            mem_cfg = self.config.get("memory", {})
            self._memory = MemoryManager(
                self.simple_llm,
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
                extractor=KnowledgeExtractor(summarizer=Summarizer(llm=self.simple_llm.invoke)),
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
            self._scheduler.on_trigger = self._scheduled_trigger
            self._scheduler.start()
            init_scheduler_tool(self._scheduler)
        return self._scheduler

    def _scheduled_trigger(self, s):
        """Route a scheduled run through the coordinator (headless, 5E-2E).

        Previously a no-op. A schedule now becomes a normal background run:
        Scheduler → run_background → Coordinator → Task/Plan/Job → Runtime.
        The WebUI overrides ``on_trigger`` with its own broadcast version; this
        default executes without a UI channel. Failures are logged, never
        raised into the polling thread.

        The scheduler is an input producer, not an execution engine: it only
        supplies the schedule identity + goal and lets the coordinator own the
        Task/Plan/Job lifecycle. The conversation identity is scoped to the
        schedule (``schedule:<id>``) so a scheduled run never leaks into a
        user's conversational Brain thread. Attempts are tagged with their
        source so schedules compose with continuation/observability.
        """
        from .background import run_background

        goal = getattr(s, "goal", "") or ""
        if not goal:
            return
        try:
            run_background(
                self,
                goal,
                conversation_id=f"schedule:{s.id}",
                metadata={
                    "source": "schedule",
                    "schedule_id": s.id,
                    "schedule_description": getattr(s, "description", "") or "",
                },
            )
        except Exception as e:
            log.warning("scheduled run %s failed: %s", s.id, e)

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
                intent_detector=IntentDetector(llm=self.simple_llm),
                complexity_estimator=ComplexityEstimator(),
                evidence_detector=EvidenceDetector(llm=self.simple_llm),
                task_store=TaskStore(),
                planner_engine=PlannerEngine(),
            )
        return self._orchestrator

    # ── durable execution lifecycle (Milestone 5 Phase 4) ────────────────

    @property
    def job_store(self):
        from ..jobs.persistence import JobStore

        if self._job_store is None:
            self._job_store = JobStore()
        return self._job_store

    @property
    def job_manager(self):
        from ..jobs.manager import JobManager

        if self._job_manager is None:
            self._job_manager = JobManager(store=self.job_store)
        return self._job_manager

    @property
    def job_lifecycle(self):
        """Event coordinator: Runtime plan events → Job + Checkpoint state."""
        from .job_lifecycle import JobLifecycle

        if self._job_lifecycle is None:
            task_store = getattr(self.orchestrator, "task_store", None)
            self._job_lifecycle = JobLifecycle(
                job_manager=self.job_manager,
                task_store=task_store,
            )
        return self._job_lifecycle

    @property
    def continuation(self):
        """Read-only continuation resolver (TaskStore + JobStore join).

        Shared across every migrated execution surface (CLI, Telegram, WebUI).
        The ExecutionCoordinator consumes this to resolve "continue" through
        the SAME path as WebUI — no per-surface continuation logic.
        """
        from .continuation import ContinuationService

        if self._continuation is None:
            self._continuation = ContinuationService(
                task_store=getattr(self.orchestrator, "task_store", None),
                job_store=self.job_store,
                job_manager=self.job_manager,
            )
        return self._continuation

    # ── startup recovery (Milestone 5 Phase 6B) ───────────────────────────

    def recover_jobs(self, bus=None) -> list[dict]:
        """Application/composition startup hook: recognize interrupted work.

        Marks every persisted execution left nonterminal by a previous process
        as INTERRUPTED (preserving its checkpoint and Task/Plan references,
        emitting the established ``job.interrupted`` event). It never executes
        work and never resurrects a Job — explicit continuation through
        ``JobManager.reopen`` is the only resume path.

        ``bus`` (optional, duck-typed EventBus) carries the lifecycle events
        for passive projections (e.g. the timeline). Lives on the composition
        root so every surface (WebUI, CLI, Telegram, background, scheduler)
        benefits from the same recovery behavior; the operation is idempotent.
        """
        from .recovery import recover_interrupted_jobs

        try:
            return recover_interrupted_jobs(self.job_store, bus=bus)
        except Exception as e:
            log.warning("startup job recovery failed: %s", e)
            return []

    def recover_once(self, bus=None) -> list[dict]:
        """Idempotent recovery sweep, run at most once per CozmoContext.

        ``build_application_execution`` calls this so every non-WebUI surface
        (CLI, Telegram, TaskQueue, scheduler) shares the startup interruption
        sweep WebUI gets via ``warmup``. The ``_recovered`` flag keeps it a
        single scan per process even when the surface constructs executions
        repeatedly (Telegram builds one per message); ``mark_interrupted`` is
        already idempotent, so a second scan would be a harmless no-op but a
        wasted job-store walk.
        """
        if self._recovered:
            return []
        self._recovered = True
        return self.recover_jobs(bus=bus)

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
            memory=overrides.get("memory", self.memory),
            project_index=overrides.get("project_index", self.project_index),
            cfg=overrides.get("cfg", self.config),
            simple_llm=overrides.get("simple_llm", self.simple_llm),
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

        # Wire Job lifecycle (Phase 4): runtime stays a plan executor — this
        # coordinator derives the Job/Checkpoint side of the same events and
        # persists via JobStore + the Task's ExecutionHistory. Passive and
        # additive: non-plan runs emit no plan events, so no jobs are created.
        job_lifecycle = overrides.get("job_lifecycle", None)
        if job_lifecycle is None:
            job_lifecycle = self.job_lifecycle
        if job_lifecycle is not None:
            job_lifecycle.subscribe(runtime.event_bus)
        return runtime

    def warmup(self):
        """Eagerly initialize all services. Called at startup."""
        _ = self.model_service
        _ = self.simple_llm
        _ = self.memory
        _ = self.project_index
        _ = self.embedding_service
        self.init_knowledge_index()
        _ = self.brain
        # Startup interruption recovery (Phase 6B): recognize executions
        # abandoned by a previous process BEFORE any new surface can start one.
        # Emits job.interrupted on the Brain bus (already built above) so the
        # timeline projection observes it where connected.
        self.recover_jobs(
            bus=self._brain_event_bus if self._brain_event_bus is not None else None,
        )
        _ = self.scheduler
        log.info("CozmoContext: all services initialized")
