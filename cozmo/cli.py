import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .configuration.bootstrap import CONFIG_PATH, get_configuration

HISTORY_FILE = Path.home() / ".cozmo" / ".history"


def _ensure_default_config():
    """Create the default config file if missing (framework-owned write)."""
    import logging

    cfg = get_configuration()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg.store.write(cfg.state.as_dict())
        logging.getLogger("cozmo.cli").info(
            "created default config at %s", CONFIG_PATH)


class FileCompleter(Completer):
    """Fuzzy file completer triggered by @ prefix."""
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self._files: list[str] | None = None

    def _get_files(self) -> list[str]:
        if self._files is None:
            self._files = []
            for f in sorted(self.cwd.rglob("*")):
                if f.is_file() and ".git" not in f.parts:
                    self._files.append(str(f.relative_to(self.cwd)))
        return self._files

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if "@" not in text:
            return
        idx = text.rindex("@")
        partial = text[idx + 1:]
        partial_lower = partial.lower()
        count = 0
        for f in self._get_files():
            if partial_lower in f.lower():
                yield Completion("@" + f, start_position=-(len(partial) + 1))
                count += 1
                if count >= 20:
                    return


def _safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def _status_bar(registry) -> str:
    agent = registry.current
    n = len([m for m in agent.history if m[0] == "user"]) if hasattr(agent, "history") else 0
    name = registry.current_name
    return f"[{name} turns:{n}]"


def _handle_slash(cmd: str) -> tuple[str, bool]:
    cmd = cmd.strip().lower()
    if cmd in ("exit", "quit", "q"):
        return "", True
    return cmd, False


def _format_continuation_candidates(candidates: list) -> str:
    if not candidates:
        return "You have resumable work, but nothing is clearly continue-able."
    lines = ["Multiple pieces of work can be continued:"]
    for i, c in enumerate(candidates, 1):
        title = c.get("title", "") or c.get("task_id", "")
        progress = c.get("progress", "") or ""
        lines.append(f"  {i}. {title} ({progress} steps done)")
    lines.append("Mention which one you want to keep going on.")
    return "\n".join(lines)


def _render_run(coordinator, runtime, text: str, conversation_id: str) -> str:
    """Drive one CLI turn through the ExecutionCoordinator.

    CLI rendering stays CLI-owned: tool/trace items are ignored, the assistant
    answer is assembled from token chunks exactly like the legacy
    ``runtime.run()``. Coordinator control messages (continuation candidates /
    errors) are rendered as text.
    """
    parts = []
    for item in coordinator.run_stream(runtime, text,
                                       conversation_id=conversation_id):
        if not item:
            continue
        kind = item[0]
        if kind == "control":
            payload = item[1]
            ctype = payload.get("type")
            if ctype == "error":
                return payload.get("text", "Error")
            if ctype == "continuation_candidates":
                return _format_continuation_candidates(
                    payload.get("candidates", []))
        elif kind == "token":
            parts.append(str(item[1]))
    return "".join(parts).strip()


class CliSessionAdapter:
    """A CLI session's composition root (mirrors the WebUI ``Session``).

    Owns a session-scoped runtime + ExecutionCoordinator against a stable
    conversation identity ``cli:<session_id>``. One logical session never
    creates a second Task/Job pipeline; Task/Plan/Job/History all flow through
    the same coordinator seam as WebUI chat.
    """

    def __init__(self, ctx, *, project_index=None, auto: bool = False,
                 session_id: str = ""):
        from .services.execution import build_application_execution

        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.conversation_id = f"cli:{self.session_id}"
        self.runtime, self.coordinator, _ = build_application_execution(
            ctx, project_index=project_index, auto=auto)

    def run(self, text: str) -> str:
        return _render_run(self.coordinator, self.runtime, text,
                           self.conversation_id)

    def reset(self):
        self.runtime.reset()


def interactive_session(ctx, initial_query: str | None = None):
    ctx.init_knowledge_index()
    _ = ctx.scheduler
    session = CliSessionAdapter(ctx)
    if initial_query:
        _safe_print(f"\nCozmo: {session.run(initial_query)}\n")
    while True:
        try:
            user = input("\nYou: ")
            if user.lower() in ("exit", "quit"):
                break
            result = session.run(user)
            _safe_print(f"Cozmo: {result}")
        except (EOFError, KeyboardInterrupt):
            break


def coding_session(ctx, project_path: Path, query: str | None = None, auto: bool = False):
    from .code_indexer import ProjectIndex

    ctx.init_knowledge_index()
    _ = ctx.scheduler
    session = CliSessionAdapter(
        ctx, project_index=ProjectIndex(project_path), auto=auto)

    if query:
        _safe_print(f"\nCozmo: {session.run(query)}\n")
        return

    HistoryFile = HISTORY_FILE
    HistoryFile.parent.mkdir(parents=True, exist_ok=True)

    kb = KeyBindings()

    @kb.add("f2")
    def _(event):
        print(f"\n → switched mode")
        event.app.current_buffer.text = ""
        event.app.invalidate()

    session_prompt = PromptSession(
        history=FileHistory(str(HistoryFile)),
        completer=FileCompleter(project_path),
        complete_while_typing=True,
        key_bindings=kb,
    )

    print(f"Session in {project_path}. /help for commands, F2 to switch mode.")
    while True:
        try:
            line = session_prompt.prompt(f"\nYou: ")
        except (EOFError, KeyboardInterrupt):
            break

        # !cmd shell passthrough
        if line.startswith("!"):
            cmd = line[1:].strip()
            _safe_print(f"$ {cmd}")
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
                out = r.stdout or r.stderr or "(no output)"
                _safe_print(out)
            except Exception as e:
                _safe_print(f"Error: {e}")
            continue

        # / slash commands
        if line.startswith("/"):
            cmd_raw, should_exit = _handle_slash(line[1:])
            if should_exit:
                break
            cmd = cmd_raw
            if cmd in ("new", "clear"):
                session.reset()
                print("Session cleared.")
            elif cmd == "compact":
                session.reset()
                print("History cleared.")
            elif cmd == "help":
                print(
                    "Commands:\n"
                    "  /help           Show this help\n"
                    "  /new            Clear session\n"
                    "  /exit           Quit\n"
                    "  /compact        Clear history\n"
                    "  @file           Attach file to context\n"
                    "  !command        Run shell command"
                )
            else:
                print(f"Unknown: /{cmd}")
            continue

        if not line.strip():
            continue

        result = session.run(line)
        _safe_print(f"Cozmo: {result}")


def run_telegram(ctx):
    from .services.telegram import build_telegram_bot

    token = ctx.config.get("telegram", {}).get("bot_token", "")
    if not token:
        print("Error: telegram.bot_token not set in config")
        return

    allowed = ctx.config.get("telegram", {}).get("allowed_chat_ids", [])
    bot = build_telegram_bot(ctx, token, allowed_chat_ids=allowed)
    print("Cozmo Telegram bot started. Press Ctrl+C to stop.")
    bot.run()


def main():
    parser = argparse.ArgumentParser("cozmo")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Generate ~/.cozmo/config.toml")
    sub.add_parser("telegram", help="Run Cozmo as Telegram bot")

    run_parser = sub.add_parser("run", help="Run a query or start interactive session")
    run_parser.add_argument("query", nargs="?", help="Single query (omit for interactive)")

    code_parser = sub.add_parser("code", help="Start a coding session with project tools")
    code_parser.add_argument("query", nargs="?", help="Single query (omit for interactive)")
    code_parser.add_argument("--path", default=".", help="Project directory")
    code_parser.add_argument("--init", action="store_true", help="Index project into Chroma")
    code_parser.add_argument("--auto", action="store_true", help="Non-interactive — allow all permission prompts")

    webui_parser = sub.add_parser("webui", help="Launch the WebUI server (primary interface)")
    webui_parser.add_argument("--host", default="127.0.0.1")
    webui_parser.add_argument("--port", type=int, default=8765)

    config_parser = sub.add_parser("config", help="Manage configuration")
    config_parser.add_argument("action", choices=["show", "set", "reset"], nargs="?")
    config_parser.add_argument("key", nargs="?", help="Config key (e.g. models.coder)")
    config_parser.add_argument("value", nargs="?", help="Config value")

    mcp_parser = sub.add_parser("mcp", help="Manage MCP server connections")
    mcp_parser.add_argument("action", choices=["connect", "list", "disconnect"], nargs="?", default="connect")
    mcp_parser.add_argument("--server", help="Specific server name")

    migrate_parser = sub.add_parser("migrate", help="Migrate persistent data between versions")
    migrate_parser.add_argument("target", help="Target version (e.g. v1-to-v2)")

    rebuild_parser = sub.add_parser("rebuild", help="Rebuild the memory database for the current embedding backend")
    rebuild_parser.add_argument("--home", default=None, help="Profile directory to rebuild (default ~/.cozmo)")

    args = parser.parse_args()

    ctx = None

    if args.command == "init":
        _ensure_default_config()
        print(f"Config created at {CONFIG_PATH}")

    elif args.command == "telegram":
        from .services import CozmoContext
        ctx = CozmoContext()
        run_telegram(ctx)

    elif args.command == "run":
        from .services import CozmoContext
        ctx = CozmoContext()
        interactive_session(ctx, args.query)

    elif args.command == "code":
        from .code_indexer import ProjectIndex
        from .services import CozmoContext

        ctx = CozmoContext()
        project_path = Path(args.path).resolve()
        if args.init:
            idx = ProjectIndex(project_path)
            n = idx.index_all()
            print(f"Indexed {n} files in {project_path}")
            return
        coding_session(ctx, project_path, args.query, auto=args.auto)

    elif args.command == "webui":
        from .ollama import is_ollama_running, start_ollama, stop_ollama, wait_for_ollama
        from .webui_server import run_server
        from .services import CozmoContext

        ctx = CozmoContext()
        ollama_url = ctx.config.get("ollama", {}).get("url", "http://localhost:11434")
        proc = None
        if not is_ollama_running():
            print("Starting Ollama...")
            proc = start_ollama(ollama_url)
            if proc:
                if not wait_for_ollama(ollama_url):
                    print("Warning: Ollama didn't respond in time. It may still be starting.")
            else:
                print("Continuing without Ollama.")

        try:
            print(f"Cozmo WebUI at http://{args.host}:{args.port}")
            run_server(ctx.config, host=args.host, port=args.port)
        finally:
            if proc:
                stop_ollama(proc)

    elif args.command == "mcp":
        from .runtime.mcp.runtime_client import MCPRuntimeClient
        import asyncio

        async def _run_mcp():
            cfg = get_configuration()
            mcp_cfg = cfg.get("mcp", {}).get("servers", {}) or {}
            from .tools import TOOL_REGISTRY
            if args.action == "connect":
                for name, server_cfg in mcp_cfg.items():
                    print(f"[mcp] Connecting to {name}...")
                    client = MCPRuntimeClient(name)
                    try:
                        await client.connect(server_cfg)
                    except Exception as e:
                        print(f"[mcp] Failed to connect to {name}: {e}")
                        continue
                    wrappers = await client.list_tools()
                    print(f"[mcp] Got {len(wrappers)} tool wrappers from {name}")
                    for w in wrappers:
                        TOOL_REGISTRY[w.__name__] = w
                        print(f"  Registered: {w.__name__}")
                    await client.close()
            elif args.action == "list":
                for name in mcp_cfg:
                    print(f"  {name}")
            elif args.action == "disconnect":
                for name in mcp_cfg:
                    print(f"  {name}")

        asyncio.run(_run_mcp())

    elif args.command == "migrate":
        if args.target == "v1-to-v2":
            from .migrate import migrate
            migrate()
        else:
            print(f"Unknown migration target: {args.target}")

    elif args.command == "rebuild":
        from .memory.rebuild import rebuild
        home = args.home or str(Path.home() / ".cozmo")
        report = rebuild(home)
        print(f"removed {len(report['removed'])} vector store(s)")
        for r in report["removed"]:
            print(f"  - {r}")
        print(f"knowledge index rebuilt: {report['reindexed']} documents")

    elif args.command == "config":
        from .config_cli import handle_config
        handle_config(args)

    else:
        # Default: launch webui
        from .ollama import is_ollama_running, start_ollama, stop_ollama, wait_for_ollama
        from .webui_server import run_server
        from .services import CozmoContext

        ctx = CozmoContext()
        ollama_url = ctx.config.get("ollama", {}).get("url", "http://localhost:11434")
        proc = None
        if not is_ollama_running():
            print("Starting Ollama...")
            proc = start_ollama(ollama_url)
            if proc:
                if not wait_for_ollama(ollama_url):
                    print("Warning: Ollama didn't respond in time. It may still be starting.")
            else:
                print("Continuing without Ollama.")

        try:
            print(f"Cozmo WebUI at http://127.0.0.1:8765")
            run_server(ctx.config, host="127.0.0.1", port=8765)
        finally:
            if proc:
                stop_ollama(proc)


if __name__ == "__main__":
    main()
