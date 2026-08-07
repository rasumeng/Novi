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
)

ROLES = ["classifier", "router", "orchestrator", "chat", "coder", "planner", "vision"]

_EXPERIENCE_OPTIONS = [
    Option("light", "Light", "Fastest responses, smallest footprint"),
    Option("medium", "Medium", "Best balance of speed, quality, and memory"),
    Option("heavy", "Heavy", "Maximum quality on powerful hardware"),
    Option("custom", "Custom", "Full manual control over routing"),
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
        category=Category.ADVANCED,
        owner="runtime",
        description="How Cozmo executes.",
        settings=[
            Setting(
                id="runtime.max_steps",
                label="Max steps",
                category=Category.ADVANCED,
                owner="runtime",
                type=SettingType.INT,
                default=8,
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="runtime.max_history",
                label="Max history turns",
                category=Category.ADVANCED,
                owner="runtime",
                type=SettingType.INT,
                default=10,
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="runtime.max_tool_output_chars",
                label="Max tool output chars",
                category=Category.ADVANCED,
                owner="runtime",
                type=SettingType.INT,
                default=8000,
                visibility=Visibility.ADVANCED,
            ),
            Setting(
                id="runtime.temperatures.chat",
                label="Chat temperature",
                category=Category.ADVANCED,
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
        category=Category.ADVANCED,
        owner="tools",
        description="Tool permission modes.",
        settings=[
            Setting(
                id="permissions.write_file",
                label="Write file permission",
                category=Category.ADVANCED,
                owner="tools",
                type=SettingType.ENUM,
                default="ask",
                options=[Option("ask", "Ask", ""), Option("allow", "Allow", "Never ask"),
                         Option("deny", "Deny", "Block")],
                visibility=Visibility.ADVANCED,
            ),
        ],
    ))

    reg.register_group(SettingGroup(
        key="mcp.servers",
        label="MCP Connectors",
        category=Category.ADVANCED,
        owner="mcp",
        description="Model Context Protocol servers.",
        settings=[
            Setting(
                id="mcp.enabled",
                label="MCP enabled",
                category=Category.ADVANCED,
                owner="mcp",
                type=SettingType.BOOL,
                default=True,
                visibility=Visibility.ADVANCED,
                restart_required=True,
            ),
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