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

ROLES = ["classifier", "router", "orchestrator", "chat", "coder", "planner", "vision"]

_EXPERIENCE_OPTIONS = [
    Option("light", "Light", "Fastest responses, smallest footprint"),
    Option("medium", "Medium", "Best balance of speed, quality, and memory"),
    Option("heavy", "Heavy", "Maximum quality on powerful hardware"),
    Option("custom", "Custom", "Full manual control over routing"),
]

_MODE_OPTIONS = [
    Option("automatic", "Automatic", "Cozmo resolves installed models for every role"),
    Option("custom", "Custom", "Full manual control over model assignment"),
]

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
        description="Which models Cozmo runs and how they map to roles.",
        settings=[
            Setting(
                id="experience",
                label="Experience",
                description="How Cozmo behaves on this machine.",
                category=Category.GENERAL,
                owner="runtime",
                type=SettingType.ENUM,
                default="medium",
                options=_EXPERIENCE_OPTIONS,
            ),
            Setting(
                id="llm.default_model",
                label="Default model",
                description="Model used for anything not assigned to a role.",
                category=Category.GENERAL,
                owner="runtime",
                type=SettingType.MODEL,
                default="",
            ),
            *[
                Setting(
                    id=f"llm.roles.{role}.model",
                    label=_ROLE_LABEL[role],
                    description=_ROLE_DESC[role],
                    category=Category.DEVELOPER,
                    owner="runtime",
                    type=SettingType.MODEL,
                    default="",
                    visibility=Visibility.DEVELOPER,
                )
                for role in ROLES
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
            Setting(
                id="llm.meta.source",
                label="Model provenance",
                description="How the current llm.roles.* state was produced: "
                            "automatic resolution or manual (custom) assignment.",
                category=Category.MODELS,
                owner="runtime",
                type=SettingType.ENUM,
                default="automatic",
                options=_MODE_OPTIONS,
                visibility=Visibility.HIDDEN,
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
                description="Powers Cozmo's memory and search.",
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
        description="How Cozmo executes.",
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
            Setting(
                id="runtime.lightweight_mode",
                label="Lightweight mode",
                category=Category.GENERAL,
                owner="runtime",
                type=SettingType.BOOL,
                default=False,
                visibility=Visibility.HIDDEN,
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
                restart_required=True,
            ),
            # Dynamic server collection: ``mcp.servers.<name>`` and its leaves
            # (command/args/env/permissions) are owned by this namespace.
            Setting(
                id="mcp.servers",
                label="MCP servers",
                category=Category.CONNECTORS,
                owner="mcp",
                type=SettingType.JSON,
                default={},
                visibility=Visibility.HIDDEN,
                namespace=True,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="memory",
        label="Memory",
        category=Category.MEMORY,
        owner="memory",
        description="How Cozmo stores and recalls conversation memory.",
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
            Setting(
                id="models.mode",
                label="Model mode",
                description="Persisted user intent: automatic resolution or "
                            "fully manual (custom) model assignment.",
                category=Category.MODELS,
                owner="runtime",
                type=SettingType.ENUM,
                default="automatic",
                options=_MODE_OPTIONS,
                visibility=Visibility.HIDDEN,
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


# Role human labels/descriptions.
_ROLE_LABEL = {
    "classifier": "Classifier", "router": "Router", "orchestrator": "Orchestrator",
    "chat": "Conversation", "coder": "Coding", "planner": "Research & planning",
    "vision": "Vision",
}
_ROLE_DESC = {
    "classifier": "Intent detection & message classification",
    "router": "Task routing and capability dispatch",
    "orchestrator": "Multi-step plan generation",
    "chat": "General conversation & Q&A",
    "coder": "Code generation & editing",
    "planner": "Deep research & task planning",
    "vision": "Image analysis & vision tasks",
}