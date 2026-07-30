import argparse
import subprocess
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from . import config

HISTORY_FILE = Path.home() / ".cozmo" / ".history"


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


def interactive_session(ctx, initial_query: str | None = None):
    ctx.init_knowledge_index()
    _ = ctx.scheduler
    runtime = ctx.create_runtime()
    if initial_query:
        _safe_print(f"\nCozmo: {runtime.run(initial_query)}\n")
    while True:
        try:
            user = input("\nYou: ")
            if user.lower() in ("exit", "quit"):
                break
            result = runtime.run(user)
            _safe_print(f"Cozmo: {result}")
        except (EOFError, KeyboardInterrupt):
            break


def coding_session(ctx, project_path: Path, query: str | None = None, auto: bool = False):
    from .code_indexer import ProjectIndex

    ctx.init_knowledge_index()
    _ = ctx.scheduler
    runtime = ctx.create_runtime(project_index=ProjectIndex(project_path))
    runtime._perms.auto = auto

    if query:
        _safe_print(f"\nCozmo: {runtime.run(query)}\n")
        return

    HistoryFile = HISTORY_FILE
    HistoryFile.parent.mkdir(parents=True, exist_ok=True)

    kb = KeyBindings()

    @kb.add("f2")
    def _(event):
        print(f"\n → switched mode")
        event.app.current_buffer.text = ""
        event.app.invalidate()

    session = PromptSession(
        history=FileHistory(str(HistoryFile)),
        completer=FileCompleter(project_path),
        complete_while_typing=True,
        key_bindings=kb,
    )

    print(f"Session in {project_path}. /help for commands, F2 to switch mode.")
    while True:
        try:
            line = session.prompt(f"\nYou: ")
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
                runtime.reset()
                print("Session cleared.")
            elif cmd == "compact":
                runtime.reset()
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

        result = runtime.run(line)
        _safe_print(f"Cozmo: {result}")


def run_telegram(ctx):
    from .telegram_bot import TelegramBot
    from .tools.telegram import set_bot_instance

    token = ctx.config.get("telegram", {}).get("bot_token", "")
    if not token:
        print("Error: telegram.bot_token not set in config")
        return

    runtime = ctx.create_runtime()
    # headless: no way to ask — 'ask' rules resolve to deny (fail safe)

    bot = TelegramBot(token, runtime)
    set_bot_instance(bot)
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

    args = parser.parse_args()

    ctx = None

    if args.command == "init":
        cfg = config.init()
        print(f"Config created at {config.CONFIG_PATH}")

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
        from .runtime.mcp_host import MCPHost
        import asyncio

        async def _run_mcp():
            cfg = config.load()
            mcp_cfg = cfg.get("mcp", {}).get("servers", {})
            mcp = MCPHost(cfg.get("mcp", {}))
            if args.action == "connect":
                print("[mcp] Connecting to servers...")
                await mcp.connect(mcp_cfg)
                wrappers = await mcp.get_tool_wrappers()
                print(f"[mcp] Got {len(wrappers)} tool wrappers")
                from .tools import TOOL_REGISTRY
                for w in wrappers:
                    TOOL_REGISTRY[w.__name__] = w
                    print(f"  Registered: {w.__name__}")
            elif args.action == "list":
                for name, _ in mcp.sessions:
                    print(f"  {name}")
            elif args.action == "disconnect":
                await mcp.disconnect()

        asyncio.run(_run_mcp())

    elif args.command == "migrate":
        if args.target == "v1-to-v2":
            from .migrate import migrate
            migrate()
        else:
            print(f"Unknown migration target: {args.target}")

    elif args.command == "config":
        from .config_cli import handle_config
        handle_config(args, config)

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
