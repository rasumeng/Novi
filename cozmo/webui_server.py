"""
Cozmo WebUI server — FastAPI bridge between the React frontend and CozmoRuntime.

WebSocket protocol (/ws/chat), JSON messages:
  client → server:
    {"type": "chat", "content": "..."}                      start a run
    {"type": "stop"}                                         abort the current run
    {"type": "permission_response", "allowed": bool}
    {"type": "plan_response", "approved": bool}
    {"type": "set_directory", "path": "..."}                 set project directory (auto-indexes)
    {"type": "set_permission_mode", "mode": "manual"}        set permission mode
    {"type": "list_projects", "search?": "..."}               list/search projects
    {"type": "get_recent_conversations", "mode?": "...", "limit?": N}  recent convos for import
    {"type": "import_from_chat", "conversation_ids": [...]}   extract context from past convos
    {"type": "create_project", "name", "instructions", "files[]", "location"}  create project
    {"type": "select_project", "project_id": "..."}           set current agent task project
    {"type": "reset"}                                        new chat (clears runtime history)
  server → client:
    {"type": "token",    "text": "..."}                      streamed answer token
    {"type": "thinking", "text": "..."}                      agent step (mode, tool runs)
    {"type": "status",   "text": "..."}                      transient status line
    {"type": "plan",     "plan": "..."}                      agent plan awaiting approval
    {"type": "tool_call",  "tool": "...", "args": {...}, "id": "..."}
    {"type": "tool_result", "tool": "...", "result": "...", "id": "...", "diff": {...}?}
    {"type": "directory_set", "path": "...", "indexed": N}
    {"type": "projects_list", "projects": [...]}
    {"type": "recent_conversations", "conversations": [...]}
    {"type": "project_created", "project": {...}, "indexed": N}
    {"type": "project_selected", "project": {...}}
    {"type": "permission_request", "tool": "...", "args": {...}}
    {"type": "done"}                                         run finished
    {"type": "error",    "text": "..."}
    {"type": "assistant_event", "entry": {...}}              brain event surfaced to timeline

The runtime loop is synchronous, so each run executes in a worker thread and
events are marshalled back onto the event loop through an asyncio.Queue.
"""

import asyncio
import json
import os
import re
import uuid
import threading
import mimetypes
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .tools import TOOL_REGISTRY
from .brain.types import Turn
from .runtime.runtime import CozmoRuntime
from .runtime.interface import RuntimeInterface
from .runtime.event_bus import EventBus
from .runtime.retrieval_budget import ContextAllocation
from .runtime.sources import MemoryRetrievalSource
from .services.context import CozmoContext
from .runtime.tool_risk import get_tool_risk, risk_to_label
from .timeline import TimelineService, build_knowledge_overview
from .webui import WebUIBackend

DIST_DIR = Path(__file__).parent / "webui" / "dist"
CHATS_DIR = Path.home() / ".cozmo" / "chats"
ATTACHMENTS_DIR = Path.home() / ".cozmo" / "attachments"
SKILLS_DIR = Path.home() / ".cozmo" / "skills"
DEFAULT_SKILLS_DIR = Path(__file__).parent / "default_skills"


def _memory_items_to_dicts(result) -> list[dict]:
    """Translate a ``MemoryRetrievalSource`` result back to flat memory dicts.

    The adapter normalizes ``distance`` into item metadata; restore the store's
    flat shape (id/text/distance/score/metadata) so the frontend response
    contract is unchanged.
    """
    out = []
    for it in result.items:
        meta = {k: v for k, v in it.metadata.items() if k != "distance"}
        out.append({
            "id": it.id,
            "text": it.text,
            "distance": it.metadata.get("distance", 0.5),
            "score": it.score,
            "metadata": meta,
        })
    return out

# The expensive backend (models, tool registry, MCP connections, memory,
# skills) is built exactly once and shared by every WebSocket session.
# Each session gets a cheap per-session CozmoRuntime that references it.
_shared_backend: dict | None = None
_backend_lock = threading.Lock()

# Background agent runs (run_id -> run info + thread).
_background_runs: dict[str, dict] = {}
_background_runs_lock = threading.Lock()

# Each connection stores a thread-safe send wrapper for broadcasts.
# Key = loop-generated id, Value = callable(text: str) -> None
_connection_senders: dict[int, Callable[[str], None]] = {}
_connection_senders_lock = threading.Lock()
_next_conn_id = 0


def _register_sender(loop, ws) -> int:
    """Register a WebSocket for background-run broadcasts. Returns conn_id."""
    global _next_conn_id
    async def _send(text: str):
        try:
            await ws.send_text(text)
        except Exception:
            pass
    def send(text: str):
        asyncio.run_coroutine_threadsafe(_send(text), loop)
    with _connection_senders_lock:
        cid = _next_conn_id
        _next_conn_id += 1
        _connection_senders[cid] = send
    return cid


def _unregister_sender(cid: int):
    with _connection_senders_lock:
        _connection_senders.pop(cid, None)


def _broadcast_sync(payload: dict):
    """Synchronous broadcast — queues the send on each connection's event loop."""
    cleaned = {k: v for k, v in payload.items() if v is not None}
    text = json.dumps(cleaned)
    with _connection_senders_lock:
        dead_ids: list[int] = []
        for cid, send_fn in _connection_senders.items():
            try:
                send_fn(text)
            except Exception:
                dead_ids.append(cid)
        for cid in dead_ids:
            _connection_senders.pop(cid, None)


# ── Background run helpers ────────────────────────────────────────────────

def _start_background_run(goal: str, cfg: dict) -> str:
    """Start a coordinator-backed background run in a thread. Returns run_id.

    Milestone 5 Phase 5E-2D: Task/Plan/Job/ExecutionHistory are owned by the
    ExecutionCoordinator (``services.background.run_background``). This
    function only broadcasts the streamed items for the WebUI. No Job is
    submitted here — there is no fake ``schedule-<run_id>`` task id anymore.
    """
    import uuid
    from .services.background import run_background

    run_id = f"bg-{uuid.uuid4().hex[:8]}"
    stop_flag = threading.Event()

    def _emit(event_type: str, **kw):
        kw["type"] = event_type
        kw["run_id"] = run_id
        _broadcast_sync(kw)

    def _worker():
        try:
            b = get_backend(cfg)
            ctx = b.get("context")
            _emit("background_run_update", status="running", goal=goal)

            def on_event(item):
                if not item:
                    return
                kind = item[0]
                if kind == "tool_call":
                    _, name, args, call_id = item[:4]
                    _emit("background_run_log", log_type="tool_call",
                          tool=name, args=args, call_id=call_id)
                elif kind == "tool_result":
                    _, name, result, call_id = item[:4]
                    _emit("background_run_log", log_type="tool_result",
                          tool=name, result=result, call_id=call_id)
                elif kind == "agent_status":
                    status, goal_text = item[1], item[2]
                    step_text = item[3] if len(item) > 3 else None
                    _emit("background_run_update", status=status,
                          goal=goal_text, step=step_text)
                elif kind == "thinking":
                    _emit("background_run_update", status="running",
                          goal=goal, step=item[1])
                elif kind == "control":
                    payload = item[1]
                    if payload.get("type") == "error":
                        _emit("background_run_update", status="error",
                              goal=goal, error=payload.get("text", ""))

            result = run_background(ctx, goal, conversation_id=f"bg:{run_id}",
                                    on_event=on_event,
                                    stop_check=stop_flag.is_set,
                                    metadata={
                                        "source": "background",
                                        "run_id": run_id,
                                    })
            # Traceability: link the WebUI run to the real Task/Job.
            with _background_runs_lock:
                info = _background_runs.get(run_id)
                if info:
                    info["task_id"] = result.task_id
                    info["job_id"] = result.job_id
            if stop_flag.is_set():
                _emit("background_run_update", status="cancelled", goal=goal)
            else:
                _emit("background_run_update", status="done", goal=goal)
        except Exception as e:
            import traceback
            _emit("background_run_update", status="error", goal=goal, error=str(e))
            traceback.print_exc()
        finally:
            with _background_runs_lock:
                info = _background_runs.get(run_id)
                if info:
                    info["status"] = "done"
                    info["ended"] = datetime.now(timezone.utc).isoformat()

    info = {
        "run_id": run_id,
        "goal": goal,
        "status": "queued",
        "created": datetime.now(timezone.utc).isoformat(),
        "ended": "",
        "logs": [],
    }
    with _background_runs_lock:
        _background_runs[run_id] = info

    t = threading.Thread(target=_worker, daemon=True)
    with _background_runs_lock:
        _background_runs[run_id]["thread"] = t
    t.start()
    return run_id


def _stop_background_run(run_id: str):
    with _background_runs_lock:
        info = _background_runs.get(run_id)
        if not info:
            return
        info["status"] = "cancelled"


def _list_background_runs() -> list[dict]:
    with _background_runs_lock:
        runs = []
        for info in _background_runs.values():
            runs.append({
                "run_id": info["run_id"],
                "goal": info["goal"],
                "status": info["status"],
                "created": info["created"],
                "ended": info.get("ended", ""),
                "task_id": info.get("task_id", ""),
                "job_id": info.get("job_id", ""),
            })
        runs.sort(key=lambda r: r["created"], reverse=True)
        return runs


def _get_background_run_logs(run_id: str) -> list[dict]:
    with _background_runs_lock:
        info = _background_runs.get(run_id)
        if not info:
            return []
        return info.get("logs", [])


# ── Scheduler ────────────────────────────────────────────────────────────
# The application scheduler is the CozmoContext singleton (see _ensure_scheduler);
# its trigger dispatches through the shared background/coordinator path.

_scheduler_lock = threading.Lock()
_scheduler_inst = None


def _ensure_scheduler(cfg: dict):
    global _scheduler_inst
    with _scheduler_lock:
        if _scheduler_inst is not None:
            return _scheduler_inst
        b = get_backend(cfg)
        ctx = b.get("context")
        # Single application scheduler: reuse the composition-root singleton
        # instead of building a second polling instance (5E-2E). The default
        # trigger already routes through the coordinator; here we swap in the
        # WebUI variant that also broadcasts to connected sockets.
        sched = ctx.scheduler
        sched.on_trigger = lambda s: _start_background_run(s.goal, cfg)
        _scheduler_inst = sched
        return _scheduler_inst


def get_backend(cfg: dict) -> dict:
    """Build the shareable backend once; reuse it for every session."""
    global _shared_backend
    with _backend_lock:
        if _shared_backend is not None:
            return _shared_backend

        web_ui = WebUIBackend(cfg)
        _shared_backend = web_ui.build_backend()
        _shared_backend["timeline_service"] = _build_timeline_bridge(_shared_backend)
        return _shared_backend


def _build_timeline_bridge(backend: dict) -> TimelineService | None:
    """Wire the read-only Brain event bridge (Milestone 4).

    Subscribes the TimelineService to the Brain's own event bus (exposed
    read-only via the context). Surfaced events are persisted and forwarded to
    every connected WebSocket as ``assistant_event`` messages. This never
    touches Brain internals or moves EventBus ownership.
    """
    ctx = backend.get("context")
    if ctx is None:
        return None
    try:
        bus = ctx.brain_event_bus
        if bus is None:
            return None
        service = TimelineService(
            bus,
            on_entry=lambda entry: _broadcast_sync(
                {"type": "assistant_event", "entry": entry}
            ),
        )
        return service.start()
    except Exception as e:
        print(f"[timeline] bridge disabled: {e}")
        return None


def _safe_child(base: Path, name: str, suffix: str = "") -> Path:
    """Resolve base/name+suffix and reject any path escaping base (traversal)."""
    p = (base / f"{name}{suffix}").resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError("invalid name")
    return p

def build_runtime(cfg: dict):
    """Construct a per-session runtime cheaply from the shared backend."""
    b = get_backend(cfg)
    event_bus = EventBus()
    runtime = CozmoRuntime(
        model_service=b["model_service"],
        memory=b["memory"],
        registry=b["registry"],
        project_index=b["project_index"],
        cfg=cfg,
        router_llm=b["router_llm"],
        skills=b["skills"],
        event_bus=event_bus,
        brain=b.get("brain"),
        orchestrator=b.get("orchestrator"),
    )
    # Drive Task lifecycle from runtime plan events; runtime never touches the
    # store itself.
    from .orchestrator.projection import TaskLifecycleProjection
    orch = b.get("orchestrator")
    task_store = getattr(orch, "task_store", None) if orch else None
    TaskLifecycleProjection(task_store).subscribe(event_bus)
    return runtime, b["orchestrator"], b["job_manager"], event_bus


def seed_default_skills():
    """Copy default skills (e.g. skill-creator) into ~/.cozmo/skills/ on first run."""
    import shutil
    if not DEFAULT_SKILLS_DIR.exists():
        return
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for folder in DEFAULT_SKILLS_DIR.iterdir():
        if not folder.is_dir():
            continue
        target = SKILLS_DIR / folder.name
        if target.exists():
            continue
        shutil.copytree(str(folder), str(target), dirs_exist_ok=True)

class Session:
    """One WebSocket connection = one runtime + one run at a time."""

    def __init__(self, cfg: dict, loop: asyncio.AbstractEventLoop):
        self.runtime, self.orchestrator, self.job_manager, self.event_bus = build_runtime(cfg)
        self.loop = loop
        self.events: asyncio.Queue = asyncio.Queue()
        self.stop_flag = threading.Event()
        self.runtime.set_config(stop_event=self.stop_flag)
        self._perm_event = threading.Event()
        self._perm_allowed = False
        self._plan_event = threading.Event()
        self._plan_approved = False
        self._worker: threading.Thread | None = None
        self.current_conv_id = ""
        self.current_job_id = ""
        self.current_task_id = ""
        self.agent_config: dict = {}

        b = get_backend(cfg)
        self.task_store = getattr(self.orchestrator, "task_store", None)
        self.continuation = b.get("continuation")

        # Milestone 5 Phase 5E-1: Session.start_run delegates to the shared
        # ExecutionCoordinator (single ownership of Task/Plan/Job/Runtime).
        # This process wires the job the coordinator creates.
        from .services.execution import ExecutionCoordinator

        self.coordinator = ExecutionCoordinator(
            orchestrator=self.orchestrator,
            job_manager=self.job_manager,
            task_store=self.task_store,
            continuation=self.continuation,
        )

        # Bridge EventBus→WebSocket: forward runtime events
        self.event_bus.on_any(self._on_bus_event)

    def _on_bus_event(self, event):
        """Forward EventBus events as WebSocket messages."""
        t = event.type
        d = event.data
        if t == "tool_called":
            self._emit({"type": "tool_call", "tool": d.get("tool", ""),
                        "args": d.get("args", {}), "id": d.get("call_id", ""),
                        "category": d.get("category", "")})
        elif t == "tool_result":
            self._emit({"type": "tool_result", "tool": d.get("tool", ""),
                        "result": d.get("output", ""), "id": d.get("call_id", ""),
                        "diff": d.get("diff")})

    def apply_agent_config(self):
        if not self.agent_config:
            return
        ac = self.agent_config
        config = {}
        model = ac.get("model")
        if model:
            config["force_model"] = model
        if "max_steps" in ac:
            config["max_steps"] = int(ac["max_steps"])
        if "temperature" in ac:
            config["temperature"] = float(ac["temperature"])
        config["agent_system_extra"] = ac.get("system_prompt", "")
        self.runtime.set_config(**config)

    # runs in worker thread
    def _emit(self, payload: dict):
        cleaned = {k: v for k, v in payload.items() if v is not None}
        self.loop.call_soon_threadsafe(self.events.put_nowait, cleaned)

    # runs in worker thread — block until the browser answers
    def _ask_permission(self, tool: str, args: dict) -> bool:
        self._perm_event.clear()
        self._emit({"type": "permission_request", "tool": tool, "args": args})
        if not self._perm_event.wait(timeout=120):
            return False
        return self._perm_allowed

    def answer_permission(self, allowed: bool):
        self._perm_allowed = bool(allowed)
        self._perm_event.set()

    # runs in worker thread — block until the browser approves/rejects plan
    def _ask_plan(self, plan_text: str) -> bool:
        self._plan_event.clear()
        self._emit({"type": "plan", "plan": plan_text})
        if not self._plan_event.wait(timeout=300):
            return False
        return self._plan_approved

    def answer_plan(self, approved: bool):
        self._plan_approved = approved
        self._plan_event.set()

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _resolve_attachments(self, attachments_meta: list[dict]) -> list[dict]:
        resolved = []
        for a in attachments_meta:
            entry = dict(a)
            att_id = a.get("id", "")
            for p in ATTACHMENTS_DIR.iterdir():
                if p.stem == att_id and p.is_file():
                    entry["path"] = str(p)
                    break
            resolved.append(entry)
        return resolved

    def start_run(self, user_input: str, attachments_meta: list[dict] | None = None, project_context: str | None = None):
        self.stop_flag.clear()
        resolved_atts = self._resolve_attachments(attachments_meta) if attachments_meta else None
        self.runtime.set_config(project_context=project_context or "")

        def work():
            try:
                for item in self.coordinator.run_stream(
                    runtime=self.runtime,
                    user_input=user_input,
                    conversation_id=self.current_conv_id,
                    attachments=resolved_atts,
                    stop_check=self.stop_flag.is_set,
                ):
                    if item and item[0] == "control":
                        payload = item[1]
                        self._emit(payload)
                    else:
                        self._forward_item(item)
                self.current_job_id = self.coordinator.job_id or self.current_job_id
                self.current_task_id = self.coordinator.task_id or self.current_task_id
                if self.coordinator.mode == "ambiguous":
                    self._emit({"type": "continuation_candidates",
                                "candidates": self.coordinator.candidates})
            except Exception as e:
                self._emit({"type": "error", "text": str(e)})
                job_id = self.coordinator.job_id or getattr(self, "current_job_id", None)
                if job_id:
                    self.job_manager.complete(job_id, error=str(e))
            finally:
                self._emit({"type": "done"})

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _forward_item(self, item: tuple):
        """Stream a runtime event tuple to the WebSocket."""
        kind = item[0]
        if kind == "tool_call":
            _, name, args, call_id = item[:4]
            payload = {"type": "tool_call", "tool": name, "args": args, "id": call_id}
            if len(item) >= 5 and item[4] is not None:
                payload["category"] = item[4]
            self._emit(payload)
        elif kind == "tool_result":
            _, name, result, call_id = item[:4]
            payload = {"type": "tool_result", "tool": name, "result": result, "id": call_id}
            if len(item) >= 5 and item[4] is not None:
                payload["diff"] = item[4]
            self._emit(payload)
        elif kind == "reasoning":
            self._emit({"type": "reasoning", "text": item[1]})
        elif kind == "trace":
            event = item[1]
            payload = event.to_dict()
            payload["type"] = "trace"
            self._emit(payload)
        else:
            text = item[1]
            detail = item[2] if len(item) > 2 else None
            query = item[3] if len(item) > 3 else None
            self._emit({"type": kind, "text": text, "detail": detail, "query": query})

    def stop(self):
        self.stop_flag.set()
        self._perm_allowed = False
        self._perm_event.set()
        self._plan_approved = False
        self._plan_event.set()


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or config.load()
    app = FastAPI(title="Cozmo WebUI")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Conversation persistence ──────────────────────────────

    CHATS_DIR.mkdir(parents=True, exist_ok=True)

    def _conversations_idx():
        idx = CHATS_DIR / "index.json"
        if idx.exists():
            raw = json.loads(idx.read_text("utf-8"))
            if "conversations" not in raw:
                raw["conversations"] = []
            return raw
        return {"conversations": []}

    def _save_idx(idx: dict):
        (CHATS_DIR / "index.json").write_text(json.dumps(idx, indent=2), "utf-8")

    def _conv_to_file(conv: dict):
        mode = conv.get("mode", "chat")
        title = conv.get("title", "Untitled")
        lines = [f"# {title}", f"mode: {mode}", ""]
        for m in conv.get("messages", []):
            role = "User" if m.get("role") == "user" else "Cozmo"
            lines.append(f"## {role}")
            lines.append(m.get("content", ""))
            model = m.get("model")
            if model:
                lines.append(f"@model {model}")
            atts = m.get("attachments")
            if atts:
                lines.append(f"@attachments {json.dumps(atts)}")
            lines.append("")
        return "\n".join(lines)

    @app.get("/api/conversations")
    def get_conversations():
        idx = _conversations_idx()
        return [
            {
                "id": c["id"],
                "title": c["title"],
                "updatedAt": c.get("updatedAt", ""),
                "pinned": c.get("pinned", False),
                "mode": c.get("mode", "chat"),
                "messages": _load_messages(c["id"]),
            }
            for c in idx.get("conversations", [])
        ]

    def _load_messages(conv_id: str) -> list:
        try:
            md_path = _safe_child(CHATS_DIR, conv_id, ".md")
        except ValueError:
            return []
        if not md_path.exists():
            return []
        content = md_path.read_text("utf-8")
        messages = []
        current_role = None
        current_text = []
        for line in content.split("\n")[1:]:
            m = re.match(r"^## (User|Cozmo)$", line.strip())
            if m:
                if current_role:
                    msg = _build_msg(conv_id, current_role, current_text, len(messages))
                    messages.append(msg)
                current_role = "user" if m.group(1) == "User" else "assistant"
                current_text = []
            elif current_role:
                current_text.append(line)
        if current_role:
            msg = _build_msg(conv_id, current_role, current_text, len(messages))
            messages.append(msg)
        return messages

    def _build_msg(conv_id: str, role: str, lines: list, idx: int) -> dict:
        atts = None
        model = None
        clean = []
        for l in lines:
            if l.startswith("@attachments "):
                try:
                    atts = json.loads(l[len("@attachments "):])
                except json.JSONDecodeError:
                    clean.append(l)
            elif l.startswith("@model "):
                model = l[len("@model "):].strip()
            else:
                clean.append(l)
        msg = {"role": role,
               "content": "\n".join(clean).strip(),
               "id": f"{conv_id}-{idx}",
               "createdAt": ""}
        if atts:
            msg["attachments"] = atts
        if model:
            msg["model"] = model
        return msg

    @app.put("/api/conversations")
    def put_conversation(body: dict):
        conv_id = body.get("id", "").strip()
        title = body.get("title", "Untitled")
        pinned = body.get("pinned", False)
        mode = body.get("mode", "chat")
        messages = body.get("messages", [])

        if not conv_id:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "id required"}, status_code=400)
        try:
            md_path = _safe_child(CHATS_DIR, conv_id, ".md")
        except ValueError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "invalid id"}, status_code=400)

        ts = datetime.now(timezone.utc).isoformat()
        idx = _conversations_idx()

        existing = [c for c in idx["conversations"] if c["id"] == conv_id]
        if existing:
            entry = existing[0]
            entry["title"] = title
            entry["pinned"] = pinned
            entry["mode"] = mode
            entry["updatedAt"] = ts
        else:
            entry = {
                "id": conv_id,
                "title": title,
                "pinned": pinned,
                "mode": mode,
                "createdAt": ts,
                "updatedAt": ts,
            }
            idx["conversations"].insert(0, entry)

        _save_idx(idx)
        # write .md file
        md_path.write_text(
            _conv_to_file({"title": title, "mode": mode, "messages": messages}),
            "utf-8",
        )
        return {"ok": True}

    @app.get("/api/conversations/search")
    def search_conversations(q: str = ""):
        if not q.strip():
            return []
        ql = q.lower()
        results = []
        for c in _conversations_idx().get("conversations", []):
            md_path = CHATS_DIR / f"{c['id']}.md"
            if not md_path.exists():
                continue
            body = md_path.read_text("utf-8")
            idx = body.lower().find(ql)
            if idx < 0:
                continue
            start = max(0, idx - 60)
            end = min(len(body), idx + len(q) + 60)
            snippet = body[start:end].strip().replace("\n", " ")
            results.append({
                "id": c["id"], "title": c["title"],
                "pinned": c.get("pinned", False),
                "mode": c.get("mode", "chat"),
                "match": snippet,
            })
        return results[:20]

    @app.delete("/api/conversations/{conv_id}")
    def delete_conversation(conv_id: str):
        idx = _conversations_idx()
        idx["conversations"] = [c for c in idx["conversations"] if c["id"] != conv_id]
        _save_idx(idx)
        try:
            md_path = _safe_child(CHATS_DIR, conv_id, ".md")
        except ValueError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "invalid id"}, status_code=400)
        if md_path.exists():
            md_path.unlink()
        return {"ok": True}

    def _conversation_by_id(conv_id: str) -> dict | None:
        idx = _conversations_idx()
        for c in idx["conversations"]:
            if c["id"] == conv_id:
                return {
                    "id": c["id"],
                    "title": c["title"],
                    "updatedAt": c.get("updatedAt", ""),
                    "pinned": c.get("pinned", False),
                    "mode": c.get("mode", "chat"),
                    "messages": _load_messages(c["id"]),
                }
        return None

    # ── Config CRUD ─────────────────────────────────────────────

    from .configuration import (
        Configuration,
        UnknownSettingError,
        ValidationError,
        get_bus,
    )
    from .configuration.bootstrap import (
        get_configuration,
        register_apply_hook,
    )
    from .configuration.discovery import ModelDiscovery, query_ollama_tags
    from .configuration.catalog import ModelRecommendationEngine, build_catalog_payload
    from .configuration import presets as presets_mod
    from .configuration.install import ModelInstaller

    configuration = get_configuration()

    # Broadcast configuration changes to connected WebSocket clients.
    try:
        get_bus().on_any(lambda ev: _broadcast_sync({"type": "config_updated", "event": ev.to_dict()}))
    except Exception as e:
        print(f"[cozmo] config broadcast hook failed: {e}")

    def _sync_config_snapshot():
        # Keep the process-wide ``cfg`` dict in sync with framework state so
        # existing runtime consumers keep reading a fresh snapshot.
        cfg.clear()
        cfg.update(configuration.snapshot())

    def _sanitize_config(cfg: dict) -> dict:
        safe = {}
        for k, v in cfg.items():
            if k.startswith("_"):
                continue
            if v is None:
                continue
            if isinstance(v, dict):
                sv = _sanitize_config(v)
                if sv:
                    safe[k] = sv
            elif isinstance(v, (str, int, float, bool, list)):
                safe[k] = v
        return safe

    def _strip_none(d: dict) -> dict:
        """Remove keys with None values recursively (TOML can't serialize None)."""
        out = {}
        for k, v in d.items():
            if v is None:
                continue
            if isinstance(v, dict):
                cleaned = _strip_none(v)
                if cleaned:
                    out[k] = cleaned
            else:
                out[k] = v
        return out

    @app.get("/api/config")
    def get_config():
        _sync_config_snapshot()
        return _sanitize_config(cfg)

    @app.put("/api/config")
    def put_config(body: dict):
        """Legacy bulk-write endpoint — routed through the framework.

        Each key present in the body is applied via configuration.set (validated,
        persisted, emitted). Unknown keys with no registered setting surface a
        warning instead of silently writing raw dict state.
        """
        results = []
        for k, v in body.items():
            if registry_has(configuration, k):
                try:
                    configuration.set(k, v, by="webui")
                    results.append({"id": k, "ok": True, "value": v})
                except ValidationError as e:
                    results.append({"id": k, "ok": False, "errors": e.errors})
                except Exception as e:
                    results.append({"id": k, "ok": False, "error": str(e)})
            else:
                # Déprioritized: fall back to raw merge for keys not in schema.
                results.append({"id": k, "ok": True, "raw": True, "value": v})
        _sync_config_snapshot()
        # notify the shared backend so in-memory state matches disk
        with _backend_lock:
            backend = _shared_backend
        if backend:
            try:
                backend["mcp"].refresh_from_config(cfg)
            except Exception as e:
                print(f"[cozmo] MCP config refresh failed: {e}")
        return {"ok": True, "results": results}

    def registry_has(configuration, k: str) -> bool:
        return configuration.registry.has(k)

    # ── Configuration Framework API ────────────────────────────────

    @app.get("/api/configuration/schema")
    def get_config_schema():
        return {"settings": configuration.schema(visibility="all"), "groups": configuration.schema_groups()}

    @app.get("/api/configuration")
    def get_configuration_state():
        _sync_config_snapshot()
        return configuration.snapshot()

    @app.patch("/api/configuration")
    def patch_configuration(body: dict):
        """Apply {id: value} patches through the framework. Validated + persisted."""
        by = body.get("__by", "webui")
        patches = {k: v for k, v in body.items() if not k.startswith("_")}
        out = []
        for k, v in patches.items():
            try:
                configuration.set(k, v, by=by)
                out.append({"id": k, "ok": True, "value": configuration.get(k)})
            except UnknownSettingError:
                out.append({"id": k, "ok": False, "error": "unknown setting"})
            except ValidationError as e:
                out.append({"id": k, "ok": False, "errors": e.errors})
            except Exception as e:
                out.append({"id": k, "ok": False, "error": str(e)})
        _sync_config_snapshot()
        with _backend_lock:
            backend = _shared_backend
        if backend:
            try:
                backend["mcp"].refresh_from_config(cfg)
            except Exception as e:
                print(f"[cozmo] MCP config refresh failed: {e}")
        return out

    @app.post("/api/configuration/{setting_id}")
    def set_configuration_value(setting_id: str, body: dict):
        value = body.get("value")
        by = body.get("by", "web")
        try:
            configuration.set(setting_id, value, by=by)
            return {"ok": True, "value": configuration.get(setting_id), "setting": setting_id}
        except UnknownSettingError as e:
            return {"error": str(e)}
        except ValidationError as e:
            return {"error": e.errors}

    @app.post("/api/configuration/apply")
    def apply_configuration(body: dict):
        """Apply + apply-hooks a preset to the runtime routing, then persist."""
        preset_id = body.get("preset")
        discovered = ModelDiscovery(
            configuration.get("ollama.url", "http://localhost:11434")
        ).installed_names()
        result = presets_mod.resolve_preset(preset_id, sorted(discovered))
        if result is None:
            return {"ok": False, "error": "unknown or custom preset"}
        for role, model in result["roles"].items():
            key = f"llm.roles.{role}.model"
            if configuration.registry.has(key) and model:
                try:
                    configuration.set(key, model, by="preset")
                except Exception as e:
                    return {"ok": False, "error": f"failed to set {key}: {e}"}
        configuration.set("experience", preset_id, by="preset")
        _sync_config_snapshot()
        return {"ok": True, **result}

    # ── Ollama available models ─────────────────────────────────

    @app.get("/api/ollama/models")
    def get_ollama_models():
        url = configuration.get("ollama.url", "http://localhost:11434")
        return [m["name"] for m in query_ollama_tags(url)]

    # ── Model discovery (dynamic, status + recommendations) ─────────

    @app.get("/api/models/discovery")
    def get_model_discovery():
        """Live discovery with status (installed/available/missing) + reasons."""
        url = configuration.get("ollama.url", "http://localhost:11434")
        discovery = ModelDiscovery(url)
        installed = discovery.installed()
        payload = build_catalog_payload(installed)
        # Add availability/missing signal for models referenced in config.
        referenced = set()
        roles = configuration.get("llm.roles", {})
        for spec in roles.values():
            if isinstance(spec, dict) and spec.get("model"):
                referenced.add(spec["model"])
        emb = configuration.get("embedding.model", "")
        if emb:
            referenced.add(emb)
        installed_names = {m.name for m in installed}
        missing = [n for n in referenced if n and n not in installed_names]
        payload["missingModels"] = missing
        payload["installedNames"] = sorted(installed_names)
        payload["presets"] = presets_mod.get_presets()
        payload["activeExperience"] = configuration.get("experience", "medium")
        payload["roles"] = {
            "chat": configuration.get("llm.roles.chat.model", ""),
            "coder": configuration.get("llm.roles.coder.model", ""),
            "vision": configuration.get("llm.roles.vision.model", ""),
            "planner": configuration.get("llm.roles.planner.model", ""),
            "embedding": configuration.get("embedding.model", ""),
        }
        return payload

    @app.post("/api/models/install")
    def install_model(body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "model name required"}
        url = configuration.get("ollama.url", "http://localhost:11434")

        def progress(p):
            _broadcast_sync({"type": "install_progress", **p})

        threading.Thread(
            target=ModelInstaller(url, progress).pull,
            args=(name,),
            daemon=True,
        ).start()
        return {"ok": True, "name": name}

    # seed default skills on startup
    seed_default_skills()

    # ── Endpoints ─────────────────────────────────────────────

    @app.get("/api/models")
    def get_models():
        models = cfg.get("models", {})
        return [{"id": role, "name": name, "role": role, "active": role == "chat"}
                for role, name in models.items()]

    @app.get("/api/models/available")
    def get_available_models():
        """Return all models discovered across all configured providers."""
        try:
            b = get_backend(cfg)
            ms = b.get("model_service")
            if ms:
                by_provider = ms.list_available()
                return [
                    {"name": m.name, "provider": m.provider}
                    for provider, models in by_provider.items()
                    for m in models
                ]
        except Exception:
            pass
        return []

    @app.get("/api/tools")
    def get_tools():
        return [{"id": name, "name": name,
                 "description": (fn.__doc__ or "").strip().split("\n")[0],
                 "enabled": True,
                 "risk": get_tool_risk(name).value,
                 "risk_label": risk_to_label(get_tool_risk(name))}
                for name, fn in sorted(TOOL_REGISTRY.items())]

    # ── Memory ──────────────────────────────────────────────────

    @app.get("/api/memory/list")
    def list_memory():
        b = get_backend(cfg)
        mem = b.get("memory")
        if not mem:
            return []
        return mem.list_all(limit=200)

    @app.get("/api/memory/search")
    def search_memory(q: str = ""):
        if not q.strip():
            return []
        b = get_backend(cfg)
        mem = b.get("memory")
        if not mem:
            return []
        try:
            src = MemoryRetrievalSource(mem, distance_threshold=1.0)
            result = src.retrieve(q, ContextAllocation(max_results=10))
            return _memory_items_to_dicts(result)
        except Exception:
            return []

    @app.delete("/api/memory/{item_id}")
    def delete_memory(item_id: str):
        b = get_backend(cfg)
        mem = b.get("memory")
        if not mem:
            return {"ok": False}
        return {"ok": bool(mem.delete(item_id))}

    @app.get("/api/memory/path")
    def memory_path():
        return {"path": str(Path.home() / ".cozmo" / "memory")}

    # ── Brain / Timeline (Milestone 4) ──────────────────────────────

    @app.get("/api/timeline")
    def timeline(limit: int = 200):
        """Stored assistant activity feed, newest first.

        User-facing only: kind/title/detail/timestamp + per-row instance id.
        """
        b = get_backend(cfg)
        service = b.get("timeline_service")
        if service is None:
            return []
        try:
            return service.recent(limit=min(limit, 500))
        except Exception:
            return []

    @app.get("/api/knowledge/overview")
    def knowledge_overview():
        """What Cozmo knows — grouped, user-shaped projection.

        Only category / label / content / evidence. Never ids, scores,
        distances, embeddings, or storage paths.
        """
        b = get_backend(cfg)
        return build_knowledge_overview(b.get("brain"))

    @app.post("/api/transcribe")
    async def transcribe_audio(file: UploadFile = File(...)):
        import tempfile, os
        import speech_recognition as sr
        from pydub import AudioSegment
        data = await file.read()
        if not data:
            return {"text": ""}
        src_path = ""
        wav_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                f.write(data)
                src_path = f.name
            seg = AudioSegment.from_file(src_path)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                seg.export(f.name, format="wav")
                wav_path = f.name
            r = sr.Recognizer()
            with sr.AudioFile(wav_path) as src:
                audio = r.record(src)
            text = r.recognize_google(audio)
            return {"text": text}
        except sr.UnknownValueError:
            return {"text": ""}
        except Exception:
            return {"text": ""}
        finally:
            if src_path: os.unlink(src_path)
            if wav_path: os.unlink(wav_path)

    # ── Projects ───────────────────────────────────────────────

    PROJECTS_DIR = Path.home() / ".cozmo" / "projects"
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_INDEX = PROJECTS_DIR / "index.json"

    def _projects_idx() -> dict:
        if PROJECTS_INDEX.exists():
            raw = json.loads(PROJECTS_INDEX.read_text("utf-8"))
            if "projects" not in raw:
                raw["projects"] = []
            return raw
        return {"projects": []}

    def _save_projects_idx(idx: dict):
        PROJECTS_INDEX.write_text(json.dumps(idx, indent=2), "utf-8")

    @app.get("/api/projects")
    def get_projects():
        idx = _projects_idx()
        return idx["projects"]

    @app.post("/api/projects")
    def create_project(body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "name required"}, status_code=400)
        ts = datetime.now(timezone.utc).isoformat()
        project = {
            "id": f"proj-{uuid.uuid4().hex[:8]}",
            "name": name,
            "description": (body.get("description") or "").strip(),
            "conversationIds": [],
            "sharedContext": (body.get("sharedContext") or "").strip(),
            "createdAt": ts,
            "updatedAt": ts,
        }
        idx = _projects_idx()
        idx["projects"].insert(0, project)
        _save_projects_idx(idx)
        return project

    @app.put("/api/projects/{proj_id}")
    def update_project(proj_id: str, body: dict):
        idx = _projects_idx()
        for p in idx["projects"]:
            if p["id"] == proj_id:
                if "name" in body:
                    p["name"] = body["name"].strip() or p["name"]
                if "description" in body:
                    p["description"] = body["description"].strip()
                if "sharedContext" in body:
                    p["sharedContext"] = body["sharedContext"].strip()
                if "conversationIds" in body:
                    p["conversationIds"] = body["conversationIds"]
                p["updatedAt"] = datetime.now(timezone.utc).isoformat()
                _save_projects_idx(idx)
                return p
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.delete("/api/projects/{proj_id}")
    def delete_project(proj_id: str):
        idx = _projects_idx()
        idx["projects"] = [p for p in idx["projects"] if p["id"] != proj_id]
        _save_projects_idx(idx)
        return {"ok": True}

    @app.get("/api/projects/{proj_id}/conversations")
    def get_project_conversations(proj_id: str):
        idx = _projects_idx()
        for p in idx["projects"]:
            if p["id"] == proj_id:
                convs = []
                for cid in p.get("conversationIds", []):
                    conv = _conversation_by_id(cid)
                    if conv:
                        convs.append(conv)
                return convs
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.post("/api/directory-picker")
    def directory_picker():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askdirectory()
            root.destroy()
            return {"path": path or ""}
        except Exception as e:
            return {"path": "", "error": str(e)}

    # ── Skills ────────────────────────────────────────────────

    @app.get("/api/skills")
    def get_skills():
        if not SKILLS_DIR.exists():
            return []
        skills = []
        for folder in sorted(SKILLS_DIR.iterdir()):
            if not folder.is_dir():
                continue
            skill_file = folder / "SKILL.md"
            if not skill_file.exists():
                continue
            content = skill_file.read_text("utf-8")
            name = folder.name
            description = ""
            if content.startswith("---"):
                import yaml
                end = content.find("\n---", 3)
                if end != -1:
                    fm = yaml.safe_load(content[3:end])
                    if isinstance(fm, dict):
                        description = fm.get("description", "") or ""
            skills.append({"name": name, "description": description})
        return skills

    @app.post("/api/skills")
    def create_skill(body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "name required"}, status_code=400)
        try:
            skill_dir = _safe_child(SKILLS_DIR, name)
        except ValueError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "invalid name"}, status_code=400)
        skill_dir.mkdir(parents=True, exist_ok=True)
        content = body.get("content") or ""
        desc = body.get("description", "")
        frontmatter = f"---\nname: {name}\ndescription: \"{desc}\"\n---\n\n"
        (skill_dir / "SKILL.md").write_text(frontmatter + content, "utf-8")
        return {"name": name, "description": desc}

    @app.post("/api/skills/upload")
    async def upload_skill(file: UploadFile = File(...)):
        content = await file.read()
        text = content.decode("utf-8")
        name = Path(file.filename or "skill.md").stem
        try:
            skill_dir = _safe_child(SKILLS_DIR, name)
        except ValueError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "invalid name"}, status_code=400)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(text, "utf-8")
        # parse description from frontmatter
        description = ""
        if text.startswith("---"):
            import yaml
            end = text.find("\n---", 3)
            if end != -1:
                fm = yaml.safe_load(text[3:end])
                if isinstance(fm, dict):
                    description = fm.get("description", "") or ""
        return {"name": name, "description": description}

    @app.delete("/api/skills/{skill_name}")
    def delete_skill(skill_name: str):
        import shutil
        try:
            target = _safe_child(SKILLS_DIR, skill_name)
        except ValueError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "invalid name"}, status_code=400)
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
        return {"ok": True}

    # ── MCP Status ──────────────────────────────────────────────

    @app.get("/api/mcp/status")
    def get_mcp_status():
        if _shared_backend:
            return _shared_backend["mcp"].get_status()
        return {}

    # ── MCP Server Detail ───────────────────────────────────────

    @app.get("/api/mcp/servers/{name}")
    def get_mcp_server_detail(name: str):
        detail: dict = {"name": name}
        if _shared_backend:
            mcp_detail = _shared_backend["mcp"].get_server_detail(name)
            if mcp_detail:
                detail.update(mcp_detail)
        # merge catalog metadata if available
        from .runtime.providers.catalog import lookup_by_name
        meta = lookup_by_name(name)
        if meta:
            detail["source"] = "catalog"
            detail["description"] = meta["description"]
            detail["capabilities"] = meta["capabilities"]
            detail["category"] = meta["category"]
            detail["homepage"] = meta["homepage"]
        else:
            detail["source"] = "custom"
            detail["capabilities"] = []
        # merge permissions from config
        servers = cfg.get("mcp", {}).get("servers", {})
        server_cfg = servers.get(name)
        if server_cfg and "permissions" in server_cfg:
            detail["permissions"] = server_cfg["permissions"]
        return detail

    # ── MCP Catalog ─────────────────────────────────────────────

    @app.get("/api/mcp/catalog")
    def get_mcp_catalog():
        from .runtime.providers.catalog import get_catalog_serializable
        return get_catalog_serializable()

    # ── MCP Test ────────────────────────────────────────────────

    @app.post("/api/mcp/test")
    def test_mcp_connection(body: dict):
        name = body.get("name", "")
        if not name:
            return {"ok": False, "error": "name required"}
        servers = cfg.get("mcp", {}).get("servers", {})
        server_cfg = servers.get(name)
        if not server_cfg:
            return {"ok": False, "error": f"server '{name}' not found in config"}
        try:
            from .runtime.mcp_host import MCPHost
            import asyncio
            mcp = MCPHost({"servers": {name: server_cfg}})
            async def _test():
                await mcp.connect({name: server_cfg})
                wrappers = await mcp.get_tool_wrappers()
                await mcp.disconnect()
                return len(wrappers)
            count = asyncio.run(_test())
            return {"ok": True, "tools": count}
        except Exception as e:
            print(f"[cozmo] MCP test failed for '{name}': {e!r}")
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    # ── Attachments ────────────────────────────────────────────

    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

    MAX_UPLOAD_SIZE = 100 * 1024 * 1024

    @app.post("/api/attachments")
    async def upload_attachment(file: UploadFile = File(...)):
        ext = Path(file.filename or "file").suffix
        att_id = uuid.uuid4().hex
        filename = f"{att_id}{ext}"
        path = ATTACHMENTS_DIR / filename
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "file too large (max 100 MB)"}, status_code=413)
        path.write_bytes(content)

        mime = file.content_type or "application/octet-stream"
        is_image = mime.startswith("image/")

        att = {
            "id": att_id,
            "type": "image" if is_image else "file",
            "name": file.filename or filename,
            "mime": mime,
            "size": len(content),
            "url": f"/api/attachments/{att_id}/file",
        }

        if is_image:
            thumb_dir = ATTACHMENTS_DIR / "thumbs"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = thumb_dir / filename
            try:
                from PIL import Image as PILImage
                img = PILImage.open(path)
                img.thumbnail((128, 128))
                img.save(thumb_path)
                att["thumbnail"] = f"/api/attachments/{att_id}/thumb"
            except ImportError:
                pass

        return att

    @app.get("/api/attachments/{att_id}/file")
    def get_attachment_file(att_id: str):
        for p in ATTACHMENTS_DIR.iterdir():
            if p.stem == att_id and p.is_file():
                mime_type, _ = mimetypes.guess_type(str(p))
                from fastapi.responses import FileResponse
                return FileResponse(str(p), media_type=mime_type or "application/octet-stream")
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.get("/api/attachments/{att_id}/thumb")
    def get_attachment_thumb(att_id: str):
        thumb_dir = ATTACHMENTS_DIR / "thumbs"
        if not thumb_dir.exists():
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "not found"}, status_code=404)
        for p in thumb_dir.iterdir():
            if p.stem == att_id and p.is_file():
                from fastapi.responses import FileResponse
                return FileResponse(str(p), media_type="image/jpeg")
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.delete("/api/attachments/{att_id}")
    def delete_attachment(att_id: str):
        for p in ATTACHMENTS_DIR.iterdir():
            if p.stem == att_id and p.is_file():
                p.unlink()
                thumb = ATTACHMENTS_DIR / "thumbs" / p.name
                if thumb.exists():
                    thumb.unlink()
                return {"ok": True}
        return {"ok": True}

    # ── task queue ────────────────────────────────────────────────────────
    from .task_queue import TaskQueue, TaskStatus
    task_queue = TaskQueue()

    def _queue_execute(task):
        """Dispatch a queued prompt through the coordinator path (5E-2C).

        Returns the final answer text; links the queue record to the real
        TaskStore Task (``cozmo_task_id``) so continuation can discover it.
        """
        from .services.background import run_background
        b = get_backend(cfg)
        ctx = b.get("context")
        result = run_background(ctx, task.prompt,
                                conversation_id=f"queue:{task.id}",
                                metadata={
                                    "source": "taskqueue",
                                    "queue_id": task.id,
                                })
        task.cozmo_task_id = result.task_id
        return result.answer

    @app.get("/api/tasks")
    def list_tasks(status: str = ""):
        s = TaskStatus(status) if status else None
        return [t.to_dict() for t in task_queue.list_tasks(s)]

    @app.post("/api/tasks")
    def create_task(body: dict):
        desc = body.get("description", "")
        prompt = body.get("prompt", "")
        agent = body.get("agent_type", "general")
        if not prompt:
            return {"error": "prompt required"}
        task = task_queue.add(desc, prompt, agent)
        return task.to_dict()

    @app.post("/api/tasks/{task_id}/run")
    def run_task(task_id: str):
        task = task_queue.get(task_id)
        if not task:
            return {"error": "not found"}
        task_queue.run_task(task, _queue_execute)
        return task.to_dict()

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        ok = task_queue.cancel(task_id)
        return {"ok": ok}

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str):
        task = task_queue.get(task_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task_queue._save(task)
        return {"ok": True}

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await ws.accept()
        loop = asyncio.get_running_loop()
        conn_id = _register_sender(loop, ws)
        session = Session(cfg, loop)

        async def pump_events():
            while True:
                ev = await session.events.get()
                await ws.send_text(json.dumps(ev))

        pump = asyncio.create_task(pump_events())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")

                if mtype == "chat":
                    content = (msg.get("content") or "").strip()
                    conv_id = msg.get("conversation_id", "")
                    attachments_meta = msg.get("attachments", [])
                    project_id = msg.get("project_id", "")
                    if not content and not attachments_meta:
                        continue
                    if session.busy:
                        await ws.send_text(json.dumps(
                            {"type": "error", "text": "A run is already in progress."}))
                        continue
                    project_context = ""
                    if project_id:
                        proj_idx = _projects_idx()
                        for p in proj_idx.get("projects", []):
                            if p["id"] == project_id:
                                project_context = p.get("sharedContext", "")
                                break
                    session.current_conv_id = conv_id
                    if msg.get("mode"):
                        log.warning("client sent 'mode' field — ignored in unified pipeline")
                    session.start_run(content, attachments_meta, project_context)
                elif mtype == "agent_config":
                    session.agent_config = {k: v for k, v in msg.items() if k not in ("type",)}
                    await ws.send_text(json.dumps({"type": "agent_config", **session.agent_config}))
                elif mtype == "background_run":
                    goal = (msg.get("goal") or "").strip()
                    if not goal:
                        continue
                    run_id = _start_background_run(goal, cfg)
                    await ws.send_text(json.dumps({
                        "type": "background_run_update", "run_id": run_id,
                        "status": "queued", "goal": goal
                    }))
                elif mtype == "background_run_stop":
                    run_id = msg.get("run_id", "")
                    _stop_background_run(run_id)
                elif mtype == "background_run_list":
                    runs = _list_background_runs()
                    await ws.send_text(json.dumps({"type": "background_run_list", "runs": runs}))
                elif mtype == "background_run_logs":
                    run_id = msg.get("run_id", "")
                    logs = _get_background_run_logs(run_id)
                    await ws.send_text(json.dumps({"type": "background_run_logs", "run_id": run_id, "logs": logs}))
                elif mtype == "schedule_create":
                    goal = (msg.get("goal") or "").strip()
                    if not goal:
                        continue
                    desc = msg.get("description", "")
                    interval = int(msg.get("interval_minutes", 0))
                    _ensure_scheduler(cfg)
                    s = _scheduler_inst.add(goal, desc, interval)
                    await ws.send_text(json.dumps({"type": "schedule_created", "schedule": s.to_dict()}))
                elif mtype == "schedule_list":
                    _ensure_scheduler(cfg)
                    schedules = [s.to_dict() for s in _scheduler_inst.list()]
                    await ws.send_text(json.dumps({"type": "schedule_list", "schedules": schedules}))
                elif mtype == "schedule_delete":
                    _ensure_scheduler(cfg)
                    sid = msg.get("schedule_id", "")
                    ok = _scheduler_inst.remove(sid)
                    await ws.send_text(json.dumps({"type": "schedule_deleted", "schedule_id": sid, "ok": ok}))
                elif mtype == "schedule_toggle":
                    _ensure_scheduler(cfg)
                    sid = msg.get("schedule_id", "")
                    ok = _scheduler_inst.toggle(sid)
                    s = _scheduler_inst.get(sid)
                    await ws.send_text(json.dumps({
                        "type": "schedule_toggled", "schedule_id": sid,
                        "ok": ok, "enabled": s.enabled if s else False
                    }))
                elif mtype == "agent_memory":
                    action = msg.get("action")
                    b = get_backend(cfg)
                    brain = b.get("brain")
                    mem = b.get("memory")
                    if action == "save":
                        text = msg.get("text", "")
                        mem_type = msg.get("memory_type", "fact")
                        if text:
                            if brain is not None:
                                try:
                                    if mem_type == "preference":
                                        brain.learn(text, source="preference")
                                    elif mem_type == "fact":
                                        brain.learn(text, source="fact")
                                    else:
                                        brain.observe(Turn(user=msg.get("user", ""), assistant=text))
                                except Exception:
                                    log.warning("brain.learn failed for agent_memory save", exc_info=True)
                            elif mem:
                                if mem_type == "preference":
                                    mem.store_preference(msg.get("key", "unknown"), text)
                                elif mem_type == "fact":
                                    mem.store_fact(text, msg.get("tags"))
                                else:
                                    mem.add_interaction(msg.get("user", ""), text)
                            await ws.send_text(json.dumps({"type": "agent_memory", "action": "saved"}))
                        else:
                            await ws.send_text(json.dumps({"type": "agent_memory", "action": "error", "error": "no text"}))
                    elif action == "recall":
                        query = msg.get("query", "")
                        k = msg.get("k", 5)
                        if query:
                            # Rule #6: reads go through the Brain when wired;
                            # the memory source unwraps Brain.recall.
                            backend = brain if brain is not None else mem
                            if backend is not None:
                                src = MemoryRetrievalSource(backend)
                                result = src.retrieve(query, ContextAllocation(max_results=k))
                                results = _memory_items_to_dicts(result)
                                await ws.send_text(json.dumps({"type": "agent_memory", "action": "results", "results": results}))
                        else:
                            await ws.send_text(json.dumps({"type": "agent_memory", "action": "results", "results": []}))
                elif mtype == "agent_tasks":
                    action = msg.get("action")
                    if action == "list":
                        status_filter = msg.get("status", "")
                        s = TaskStatus(status_filter) if status_filter else None
                        tasks = task_queue.list_tasks(s)
                        await ws.send_text(json.dumps({
                            "type": "agent_tasks", "action": "list",
                            "tasks": [t.to_dict() for t in tasks]
                        }))
                    elif action == "create":
                        desc = msg.get("description", "")
                        prompt = msg.get("prompt", "")
                        agent = msg.get("agent_type", "general")
                        if prompt:
                            task = task_queue.add(desc, prompt, agent)
                            await ws.send_text(json.dumps({
                                "type": "agent_tasks", "action": "created", "task": task.to_dict()
                            }))
                        else:
                            await ws.send_text(json.dumps({
                                "type": "agent_tasks", "action": "error", "error": "prompt required"
                            }))
                elif mtype == "stop":
                    session.stop()
                elif mtype == "permission_response":
                    session.answer_permission(msg.get("allowed", False))
                elif mtype == "plan_response":
                    session.answer_plan(msg.get("approved", False))
                elif mtype == "set_directory":
                    from .code_indexer import ProjectIndex
                    from .tools.file_ops import set_allowed_root
                    path_str = msg.get("path", "")
                    if not path_str:
                        await ws.send_text(json.dumps({"type": "error", "text": "No path provided"}))
                        continue
                    try:
                        p = Path(path_str).resolve()
                        if not p.is_dir():
                            await ws.send_text(json.dumps({"type": "error", "text": f"Directory not found: {path_str}"}))
                            continue
                        set_allowed_root(p)
                        idx = ProjectIndex(p)
                        n = idx.index_all()
                        session.runtime.set_config(project_index=idx, project_context=f"Project directory: {p}")
                        await ws.send_text(json.dumps({"type": "directory_set", "path": str(p), "indexed": n}))
                    except Exception as e:
                        await ws.send_text(json.dumps({"type": "error", "text": f"Failed to index directory: {e}"}))
                elif mtype == "set_permission_mode":
                    mode = msg.get("mode", "manual")
                    session.runtime.set_config(permission_mode=mode)
                elif mtype == "list_projects":
                    idx = _projects_idx()
                    search = (msg.get("search") or "").lower()
                    raw = idx.get("projects", [])
                    if search:
                        raw = [p for p in raw if search in p["name"].lower() or search in p.get("description","").lower()]
                    await ws.send_text(json.dumps({"type": "projects_list", "projects": raw}))
                elif mtype == "get_recent_conversations":
                    idx = _conversations_idx()
                    mode_filter = msg.get("mode")
                    convs = idx.get("conversations", [])
                    if mode_filter:
                        convs = [c for c in convs if c.get("mode") == mode_filter]
                    convs.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
                    limit = msg.get("limit", 15)
                    await ws.send_text(json.dumps({"type": "recent_conversations", "conversations": convs[:limit]}))
                elif mtype == "import_from_chat":
                    conv_ids = msg.get("conversation_ids", [])
                    conv_idx = _conversations_idx()
                    conv_map = {c["id"]: c.get("title", "Untitled") for c in conv_idx.get("conversations", [])}
                    parts = []
                    titles = []
                    for cid in conv_ids:
                        md_path = _safe_child(CHATS_DIR, cid, ".md")
                        if md_path and md_path.exists():
                            text = md_path.read_text("utf-8")
                            parts.append(text[:3000])
                            if cid in conv_map:
                                titles.append(conv_map[cid])
                    combined = "\n\n---\n\n".join(parts) if parts else "(no content found)"
                    # Create project directly
                    from .code_indexer import ProjectIndex
                    base = Path.home() / ".cozmo" / "imports"
                    base.mkdir(parents=True, exist_ok=True)
                    name = f"Import: {', '.join(titles[:2])}"[:60] if titles else "Imported Chat"
                    project_dir = base / f"proj-{uuid.uuid4().hex[:8]}"
                    project_dir.mkdir(parents=True, exist_ok=True)
                    # Save instructions
                    (project_dir / ".cozmo").mkdir(exist_ok=True)
                    (project_dir / ".cozmo" / "instructions.md").write_text(combined, "utf-8")
                    # Index
                    idx_p = ProjectIndex(project_dir)
                    n = idx_p.index_all()
                    # Project record
                    ts = datetime.now(timezone.utc).isoformat()
                    project = {
                        "id": f"proj-{uuid.uuid4().hex[:8]}",
                        "name": name,
                        "description": f"Imported from {len(conv_ids)} conversation(s)",
                        "conversationIds": conv_ids,
                        "sharedContext": combined,
                        "createdAt": ts,
                        "updatedAt": ts,
                        "path": str(project_dir),
                        "indexed": n,
                    }
                    pdx = _projects_idx()
                    pdx["projects"].insert(0, project)
                    _save_projects_idx(pdx)
                    session.runtime.set_config(project_index=idx_p, project_context=f"Project: {name} at {project_dir}")
                    await ws.send_text(json.dumps({"type": "project_created", "project": project, "indexed": n}))
                elif mtype == "create_project":
                    name = (msg.get("name") or "").strip()
                    if not name:
                        await ws.send_text(json.dumps({"type": "error", "text": "Project name is required"}))
                        continue
                    location_str = (msg.get("location") or "").strip()
                    if not location_str:
                        await ws.send_text(json.dumps({"type": "error", "text": "Project location is required"}))
                        continue
                    try:
                        from .code_indexer import ProjectIndex
                        base = Path(location_str).resolve()
                        project_dir = base / name
                        project_dir.mkdir(parents=True, exist_ok=True)
                        # Save instruction file
                        instructions = msg.get("instructions", "")
                        if instructions:
                            (project_dir / ".cozmo").mkdir(exist_ok=True)
                            (project_dir / ".cozmo" / "instructions.md").write_text(instructions, "utf-8")
                        # Save uploaded files
                        files = msg.get("files", [])
                        for f in files:
                            fpath = project_dir / f["name"]
                            fpath.parent.mkdir(parents=True, exist_ok=True)
                            fpath.write_text(f.get("content", ""), "utf-8")
                        # Index the project
                        idx = ProjectIndex(project_dir)
                        n = idx.index_all()
                        # Create project record
                        ts = datetime.now(timezone.utc).isoformat()
                        project = {
                            "id": f"proj-{uuid.uuid4().hex[:8]}",
                            "name": name,
                            "description": (msg.get("description") or "").strip(),
                            "conversationIds": [],
                            "sharedContext": instructions,
                            "createdAt": ts,
                            "updatedAt": ts,
                        }
                        pidx = _projects_idx()
                        pidx["projects"].insert(0, project)
                        _save_projects_idx(pidx)
                        # Set as current agent task context
                        session.runtime.set_config(project_index=idx, project_context=f"Project: {name} at {project_dir}")
                        project["path"] = str(project_dir)
                        project["indexed"] = n
                        await ws.send_text(json.dumps({
                            "type": "project_created",
                            "project": project,
                            "indexed": n,
                        }))
                    except Exception as e:
                        await ws.send_text(json.dumps({"type": "error", "text": f"Failed to create project: {e}"}))
                elif mtype == "select_project":
                    proj_id = msg.get("project_id", "")
                    idx = _projects_idx()
                    found = None
                    for p in idx["projects"]:
                        if p["id"] == proj_id:
                            found = p
                            break
                    if found:
                        session.runtime.set_config(project_context=found.get("sharedContext", ""))
                        await ws.send_text(json.dumps({"type": "project_selected", "project": found}))
                    else:
                        await ws.send_text(json.dumps({"type": "error", "text": "Project not found"}))
                elif mtype == "reset":
                    if not session.busy:
                        session.runtime.reset()
                        await ws.send_text(json.dumps({"type": "status", "text": "New chat"}))
        except WebSocketDisconnect:
            session.stop()
        finally:
            pump.cancel()
            _unregister_sender(conn_id)

    # serve the built frontend when present (dev uses vite on :5173 instead)
    if DIST_DIR.exists():
        app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="webui")

    return app


def run_server(cfg: dict | None = None, host: str = "127.0.0.1", port: int = 8765):
    import uvicorn
    uvicorn.run(create_app(cfg), host=host, port=port)
