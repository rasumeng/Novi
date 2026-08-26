import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


class MCPHost:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.sessions: list[tuple[str, ClientSession]] = []
        self._context_managers = []

    async def connect(self, server_configs: dict | None = None):
        server_configs = server_configs or self.config.get("servers", {})
        for name, cfg in server_configs.items():
            ctx = None
            session = None
            try:
                command = cfg["command"]
                if command in ("python", "python3", "python.exe"):
                    command = sys.executable

                params = StdioServerParameters(
                    command=command,
                    args=cfg.get("args", []),
                    env=cfg.get("env"),
                )
                ctx = stdio_client(params)
                streams = await ctx.__aenter__()
                read_stream, write_stream = streams

                session = ClientSession(read_stream, write_stream)
                await session.__aenter__()
                await session.initialize()

                self.sessions.append((name, session))
                self._context_managers.append(ctx)
                print(f"[mcp] Connected to {name}")
            except Exception as e:
                if session is not None:
                    try:
                        await session.__aexit__(None, None, None)
                    except Exception:
                        pass
                if ctx is not None:
                    try:
                        await ctx.__aexit__(None, None, None)
                    except Exception:
                        pass
                print(f"[mcp] Failed to connect to {name}: {e}")

    async def disconnect(self):
        for name, session in reversed(self.sessions):
            try:
                await session.__aexit__(None, None, None)
                print(f"[mcp] Disconnected from {name}")
            except Exception as e:
                print(f"[mcp] Error disconnecting {name}: {e}")
        for ctx in reversed(self._context_managers):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self.sessions.clear()
        self._context_managers.clear()

    async def get_tool_wrappers(self) -> list[callable]:
        wrappers = []
        for server_name, session in self.sessions:
            try:
                response = await session.list_tools()
                for tool in response.tools:
                    prefixed_name = f"{server_name}_{tool.name}"
                    wrappers.append(self._make_wrapper(session, tool.name, prefixed_name))
            except Exception as e:
                print(f"[mcp] Failed to list tools: {e}")
        return wrappers

    def _make_wrapper(self, session: ClientSession, tool_name: str, display_name: str) -> callable:
        async def wrapper(**kwargs) -> str:
            try:
                result = await session.call_tool(tool_name, arguments=kwargs)
                texts = []
                for content in result.content:
                    if isinstance(content, types.TextContent):
                        texts.append(content.text)
                return "\n".join(texts) or "[no output]"
            except Exception as e:
                return f"[error] Tool call failed: {e}"
        wrapper.__name__ = display_name
        wrapper.__doc__ = f"MCP tool: {display_name}"
        return wrapper
