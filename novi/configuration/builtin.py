"""Default configuration registration — seeds the framework's registry.

Every core subsystem registers its settings here. Model names are left empty
(default="") — never hardcoded. Defaults that do not exist are resolved by
model discovery, not silently substituted.
"""

from __future__ import annotations

from .registry import ConfigRegistry
from .schema import (
    Category,
    Option,
    Setting,
    SettingGroup,
    SettingType,
    Visibility,
    require_nonempty,
    require_nonnegative_int,
)

WORKLOADS = ["general", "research", "code"]

DEFAULT_PROVIDER_OPTIONS = [
    Option("ollama", "Ollama", "Local models via Ollama"),
    Option("openai", "OpenAI", "Cloud models via OpenAI-compatible API"),
]


def register_defaults(reg: ConfigRegistry):
    """Register every core setting. Subsystems may add their own afterwards."""
    reg.register_group(SettingGroup(
        key="llm",
        label="Models",
        category=Category.MODELS,
        owner="runtime",
        description="Which models Novi runs and how they map to workloads.",
        settings=[
            *[
                Setting(
                    id=f"llm.workloads.{workload}.model",
                    label=_WORKLOAD_LABEL[workload],
                    description=_WORKLOAD_DESC[workload],
                    category=Category.MODELS,
                    owner="runtime",
                    type=SettingType.MODEL,
                    default="",
                )
                for workload in WORKLOADS
            ],
            Setting(
                id="llm.max_tokens",
                label="Max tokens",
                description="Maximum output tokens in a single response.",
                category=Category.DEVELOPER,
                owner="runtime",
                type=SettingType.INT,
                default=65536,
                visibility=Visibility.DEVELOPER,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="embedding",
        label="Memory & Embeddings",
        category=Category.MODELS,
        owner="memory",
        description="The model powering memory, retrieval, and search.",
        settings=[
            Setting(
                id="embedding.model",
                label="Embedding model",
                description="Powers Novi's memory and search.",
                category=Category.DEVELOPER,
                owner="memory",
                type=SettingType.MODEL,
                default="",
            ),
            Setting(
                id="embedding.backend",
                label="Embedding backend",
                category=Category.DEVELOPER,
                owner="memory",
                type=SettingType.ENUM,
                default="ollama",
                options=[Option("ollama", "Ollama", "local")],
                visibility=Visibility.DEVELOPER,
            ),
            Setting(
                id="embedding.dimension",
                label="Embedding dimension",
                category=Category.DEVELOPER,
                owner="memory",
                type=SettingType.INT,
                default=768,
                visibility=Visibility.DEVELOPER,
                restart_required=True,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="providers",
        label="Providers",
        category=Category.DEVELOPER,
        owner="providers",
        description="Where models come from.",
        settings=[
            Setting(
                id="providers.default",
                label="Default provider",
                description="Used when a model has no explicit provider.",
                category=Category.DEVELOPER,
                owner="providers",
                type=SettingType.ENUM,
                default="ollama",
                options=DEFAULT_PROVIDER_OPTIONS,
                visibility=Visibility.DEVELOPER,
            ),
            Setting(
                id="providers.ollama.url",
                label="Ollama URL",
                category=Category.DEVELOPER,
                owner="providers",
                type=SettingType.STRING,
                default="http://localhost:11434",
                validation=[require_nonempty],
                visibility=Visibility.DEVELOPER,
            ),
            Setting(
                id="providers.ollama.reasoning",
                label="Stream model reasoning",
                description=("Stream the model's reasoning/thinking trace to the "
                             "conversation UI when the selected model exposes one. "
                             "Models without a reasoning trace are unaffected."),
                category=Category.MODELS,
                owner="providers",
                type=SettingType.BOOL,
                default=True,
            ),
            Setting(
                id="providers.openai.api_key_env",
                label="OpenAI API key env var",
                category=Category.DEVELOPER,
                owner="providers",
                type=SettingType.SECRET,
                default="OPENAI_API_KEY",
                visibility=Visibility.DEVELOPER,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="runtime.general",
        label="Behavior",
        category=Category.DEVELOPER,
        owner="runtime",
        description="How Novi executes.",
        settings=[
            Setting(
                id="runtime.max_steps",
                label="Max steps",
                category=Category.DEVELOPER,
                owner="runtime",
                type=SettingType.INT,
                default=8,
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="runtime.max_history",
                label="Max history turns",
                category=Category.DEVELOPER,
                owner="runtime",
                type=SettingType.INT,
                default=10,
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="runtime.max_tool_output_chars",
                label="Max tool output chars",
                category=Category.DEVELOPER,
                owner="runtime",
                type=SettingType.INT,
                default=8000,
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="runtime.temperatures.chat",
                label="Chat temperature",
                category=Category.DEVELOPER,
                owner="runtime",
                type=SettingType.FLOAT,
                default=0.6,
                visibility=Visibility.ADVANCED,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="permissions",
        label="Permissions",
        category=Category.PERMISSIONS,
        owner="tools",
        description="Tool permission modes.",
        settings=[
            Setting(
                id="permissions.write_file",
                label="Write file permission",
                category=Category.PERMISSIONS,
                owner="tools",
                type=SettingType.ENUM,
                default="ask",
                options=[Option("ask", "Ask", ""), Option("allow", "Allow", "Never ask"),
                         Option("deny", "Deny", "Block")],
                visibility=Visibility.ADVANCED,
            ),
            # Dynamic per-tool / per-pattern permission rules. Any descendant
            # leaf (e.g. ``permissions.run_command``, ``permissions.mcp.*``) is
            # owned by this namespace.
            Setting(
                id="permissions",
                label="Tool permissions",
                category=Category.PERMISSIONS,
                owner="tools",
                type=SettingType.JSON,
                default={},
                visibility=Visibility.HIDDEN,
                namespace=True,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="mcp.servers",
        label="MCP Connectors",
        category=Category.CONNECTORS,
        owner="mcp",
        description="Model Context Protocol servers.",
        settings=[
            Setting(
                id="mcp.enabled",
                label="MCP enabled",
                category=Category.CONNECTORS,
                owner="mcp",
                type=SettingType.BOOL,
                default=True,
                visibility=Visibility.ADVANCED,
                restart_required=False,
            ),
            # Dynamic server collection: ``mcp.servers.<name>`` and its leaves
            # (command/args/env/permissions) are owned by this namespace. The
            # ``env`` subtree holds credentials (e.g. ``mcp.servers.<name>.env.
            # GITHUB_TOKEN``) and is classified as secret so read surfaces mask
            # values while keeping the variable names visible.
            Setting(
                id="mcp.servers",
                label="MCP servers",
                category=Category.CONNECTORS,
                owner="mcp",
                type=SettingType.JSON,
                default={},
                visibility=Visibility.HIDDEN,
                namespace=True,
                secret_segments=["env"],
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="memory",
        label="Memory",
        category=Category.MEMORY,
        owner="memory",
        description="How Novi stores and recalls conversation memory.",
        settings=[
            Setting(
                id="memory.max_turns_before_summary",
                label="Turns before summary",
                category=Category.MEMORY,
                owner="memory",
                type=SettingType.INT,
                default=5,
                validation=[require_nonnegative_int],
                visibility=Visibility.USER,
            ),
            Setting(
                id="memory.max_short_term_pairs",
                label="Recent context pairs",
                category=Category.MEMORY,
                owner="memory",
                type=SettingType.INT,
                default=10,
                validation=[require_nonnegative_int],
                visibility=Visibility.USER,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="search",
        label="Web Search",
        category=Category.CONNECTORS,
        owner="search",
        description="Which provider Novi uses for web search.",
        settings=[
            Setting(
                id="search.backend",
                label="Search provider",
                description="Brave Search works without Docker. SearXNG is a self-hosted instance.",
                category=Category.CONNECTORS,
                owner="search",
                type=SettingType.ENUM,
                default="",
                options=[
                    Option("", "Not configured", "Web search disabled"),
                    Option("brave", "Brave Search", "Official Brave Search API (needs an API key)"),
                    Option("searxng", "SearXNG", "Self-hosted SearXNG endpoint"),
                ],
                visibility=Visibility.USER,
            ),
            Setting(
                id="search.brave_api_key",
                label="Brave API key",
                description="Subscription token from the Brave Search API dashboard.",
                category=Category.CONNECTORS,
                owner="search",
                type=SettingType.SECRET,
                default="",
                visibility=Visibility.USER,
            ),
            Setting(
                id="search.url",
                label="SearXNG endpoint",
                description="Base URL of your SearXNG instance (JSON format must be enabled).",
                category=Category.CONNECTORS,
                owner="search",
                type=SettingType.STRING,
                default="http://localhost:8080",
                visibility=Visibility.USER,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="integrations",
        label="Connectors",
        category=Category.CONNECTORS,
        owner="integrations",
        description="Messaging and execution connectors.",
        settings=[
            Setting(
                id="telegram.enabled",
                label="Telegram enabled",
                category=Category.CONNECTORS,
                owner="integrations",
                type=SettingType.BOOL,
                default=False,
                visibility=Visibility.USER,
            ),
            Setting(
                id="telegram.bot_token",
                label="Telegram bot token",
                category=Category.CONNECTORS,
                owner="integrations",
                type=SettingType.SECRET,
                default="",
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="telegram.allowed_chat_ids",
                label="Allowed chat IDs",
                category=Category.CONNECTORS,
                owner="integrations",
                type=SettingType.JSON,
                default=[],
                visibility=Visibility.DEVELOPER,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="agent",
        label="Agent",
        category=Category.AGENT,
        owner="runtime",
        description="Autonomy and identity visibility (not a personality selector).",
        settings=[
            # Frontend AgentSettings edits agent.system_prompt / max_steps /
            # temperature and reads models.agent. Own the dynamic agent.* leaves
            # and the working-agent model.
            Setting(
                id="agent",
                label="Agent execution",
                category=Category.AGENT,
                owner="runtime",
                type=SettingType.JSON,
                default={},
                visibility=Visibility.HIDDEN,
                namespace=True,
            ),
            Setting(
                id="agents",
                label="Agent profiles",
                category=Category.AGENT,
                owner="runtime",
                type=SettingType.JSON,
                default={},
                visibility=Visibility.HIDDEN,
                namespace=True,
            ),
            Setting(
                id="models.agent",
                label="Agent model",
                category=Category.MODELS,
                owner="runtime",
                type=SettingType.MODEL,
                default="",
                visibility=Visibility.USER,
            ),
        ],
    ))

    # Top-level configuration roots that the legacy web UI still writes as whole
    # dicts (flushLegacy -> PUT /api/config). Registering them as namespaces
    # keeps those writes on the single authoritative config framework path
    # (validate -> persist -> apply -> emit) with no secondary raw-merge. The
    # granular children (models.agent, llm.*, runtime.*, memory.*, embedding.*,
    # mcp.servers.*, ...) are owned by the roots; unknown unknown keys still
    # surface an explicit error rather than silently writing raw state.
    reg.register_group(SettingGroup(
        key="config_roots",
        label="Configuration roots",
        category=Category.GENERAL,
        owner="runtime",
        description="Top-level namespace roots so whole-dict compatibility writes route through the framework.",
        settings=[
            Setting(id="mcp", label="MCP", category=Category.CONNECTORS,
                    owner="mcp", type=SettingType.JSON, default={},
                    visibility=Visibility.HIDDEN, namespace=True),
            Setting(id="models", label="Models", category=Category.MODELS,
                    owner="runtime", type=SettingType.JSON, default={},
                    visibility=Visibility.HIDDEN, namespace=True),
            Setting(id="llm", label="LLM", category=Category.MODELS,
                    owner="runtime", type=SettingType.JSON, default={},
                    visibility=Visibility.HIDDEN, namespace=True),
            Setting(id="runtime", label="Runtime", category=Category.GENERAL,
                    owner="runtime", type=SettingType.JSON, default={},
                    visibility=Visibility.HIDDEN, namespace=True),
            Setting(id="memory", label="Memory", category=Category.MEMORY,
                    owner="memory", type=SettingType.JSON, default={},
                    visibility=Visibility.HIDDEN, namespace=True),
            Setting(id="embedding", label="Embedding", category=Category.MEMORY,
                    owner="memory", type=SettingType.JSON, default={},
                    visibility=Visibility.HIDDEN, namespace=True),
            Setting(id="personality", label="Personality", category=Category.DEVELOPER,
                    owner="runtime", type=SettingType.STRING, default="",
                    visibility=Visibility.HIDDEN),
        ],
    )) 


# Workload human labels/descriptions. A workload is a model selection slot;
# the selected model's capabilities (vision, reasoning, tools, coding) are
# derived from the model itself, never configured here.
_WORKLOAD_LABEL = {
    "general": "General",
    "research": "Deep Research",
    "code": "Code",
}
_WORKLOAD_DESC = {
    "general": "Model used for general interaction. Vision is a capability of "
               "the selected model, not a separate selection.",
    "research": "Model used for deep research and multi-step planning tasks.",
    "code": "Model used for code generation and editing.",
}