"""Configuration manager with automatic legacy migration.

Phase C: new [llm.roles] format, auto-migration from old [models] format.
"""

import copy
import logging
import os
from pathlib import Path
import shutil
import tomllib
import tomli_w

log = logging.getLogger("cozmo.config")

CONFIG_DIR = Path.home() / ".cozmo"
CONFIG_PATH = CONFIG_DIR / "config.toml"
CONFIG_BACKUP_PATH = CONFIG_DIR / "config.toml.bak"

# ── new default (no hardcoded model names) ────────────────────────────

DEFAULT_CONFIG = {
    "llm": {
        "max_tokens": 65536,
        "default_model": "qwen3:8b",
        "roles": {
            "classifier": {"model": ""},
            "router": {"model": ""},
            "orchestrator": {"model": ""},
            "chat": {"model": ""},
            "coder": {"model": ""},
            "planner": {"model": ""},
            "vision": {"model": ""},
        },
    },
    "embedding": {
        "backend": "sentence_transformers",
        "model": "all-MiniLM-L6-v2",
    },
    "reranker": {
        "backend": "sentence_transformers",
        "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    },
    "ollama": {"url": "http://localhost:11434"},
    "providers": {
        "default": "ollama",
        "ollama": {"url": "http://localhost:11434"},
        "openai": {"api_key_env": "OPENAI_API_KEY"},
    },
    "memory": {"max_turns_before_summary": 5, "max_short_term_pairs": 10},
    "router": {
        "use_llm": False,
    },
    "workspace": {
        "path": "~/.cozmo/workspace",
        "knowledge": "~/.cozmo/knowledge",
        "git_repo": "",
    },
    "personality": "",
    "search": {
        "url": "http://localhost:8080",
        "backend": "searxng",
    },
    "mcp": {
        "servers": {},
    },
    "runtime": {
        "lightweight_mode": False,
        "max_history": 10,
        "max_steps": 8,
        "max_tool_output_chars": 8000,
        "memory_distance_threshold": 0.5,
        "max_memory_results": 3,
        "max_project_results": 3,
        "temperatures": {"chat": 0.6, "work": 0.0, "research": 0.2},
        "tool_gate": {
            "chat": ["search_knowledge", "read_knowledge", "calculator", "current_time", "search_memory"],
            "research": ["web_search", "web_search_pipeline", "web_fetch", "calculator"],
        },
        "force_capability": "",
        "force_model": "",
        "tools": {
            "fallbacks": {
                "web_search": ["web_search_pipeline"],
                "web_fetch": ["web_search_pipeline"],
            },
        },
        "planning": {
            "auto_threshold": 1,
        },
        "routing": {
            "intent_capabilities": {
                "conversation": ["conversation"],
                "research": ["research", "conversation"],
                "coding": ["coding", "filesystem", "terminal"],
                "planning": ["planning", "conversation"],
                "vision": ["vision", "conversation"],
            },
            "intent_roles": {
                "conversation": "chat",
                "research": "planner",
                "coding": "coder",
                "planning": "planner",
                "vision": "vision",
            },
            "capability_roles": {
                "coding": "coder",
                "planning": "planner",
                "research": "planner",
                "conversation": "chat",
                "vision": "vision",
            },
            "capability_preferences": {
                "coding": ["coding", "planning", "research", "conversation"],
                "planning": ["planning", "research", "coding", "conversation"],
                "research": ["research", "conversation", "coding"],
                "conversation": ["conversation", "research"],
                "vision": ["vision", "conversation"],
            },
        },
    },
    "agents": {
        "primary": ["build", "plan"],
        "build": {"model": None, "permissions": {}},
        "plan": {"model": None, "permissions": {"write_file": "deny", "edit_file": "deny", "run_command": "deny"}},
        "profiles": {
            "default": {"description": "General-purpose agent"},
            "researcher": {"description": "Research and information gathering", "tools": ["web_search", "web_search_pipeline", "web_fetch", "fetch_url", "search_knowledge", "read_knowledge", "read_file"]},
            "coder": {"description": "Code writing and editing", "model": None},
            "writer": {"description": "Documentation and prose writing"},
            "planner": {"description": "Strategic planning and architecture"},
        },
    },
    "permissions": {
        "write_file": "ask",
        "edit_file": "ask",
        "run_command": {"*": "ask", "git *": "allow", "dir *": "allow"},
    },
    "code": {"index_extensions": ["*"]},
    "desktop": {"enabled": False},
    "telegram": {"enabled": False, "bot_token": "", "allowed_chat_ids": []},
}


# ── migration ─────────────────────────────────────────────────────────

_OLD_MODEL_ROLE_MAP = {
    "chat": "chat",
    "agent": "chat",
    "coder": "coder",
    "vision": "vision",
    "research": "planner",
    "classifier": "classifier",
    "router": "router",
    "orchestrator": "orchestrator",
}


def _has_old_format(cfg: dict) -> bool:
    """Detect legacy config that has [models] but no [llm] section."""
    return "models" in cfg and "llm" not in cfg


def _migrate_from_old(cfg: dict) -> dict:
    """Convert legacy [models] config to new [llm.roles] format.

    Migration is idempotent — safe to run on already-migrated config.
    Creates a backup before writing.
    """
    old_models = cfg.pop("models", {})
    max_tokens = old_models.pop("max_tokens", 65536)

    roles = {}
    for old_key, model_name in old_models.items():
        new_role = _OLD_MODEL_ROLE_MAP.get(old_key, old_key)
        if isinstance(model_name, str) and model_name.strip():
            roles[new_role] = {"model": model_name}
        elif isinstance(model_name, dict):
            roles[new_role] = model_name
        else:
            roles[new_role] = {"model": ""}

    # Preserve any non-role keys and any existing llm section
    existing_llm = cfg.get("llm", {})
    existing_roles = existing_llm.get("roles", {})

    # Existing roles take precedence (so re-running migration is safe)
    merged_roles = {**roles, **existing_roles}

    cfg["llm"] = {
        "max_tokens": existing_llm.get("max_tokens", max_tokens),
        "roles": merged_roles,
    }

    log.info("Migrated config from [models] to [llm.roles] format")
    return cfg


def _apply_backward_compat(cfg: dict) -> dict:
    """Insert virtual [models] section when [llm.roles] is present.

    Allows legacy code paths (cfg.get('models', {})) to keep working
    without changes until they are migrated.
    """
    if "llm" in cfg and "models" not in cfg:
        roles = cfg["llm"].get("roles", {})
        models = {}
        for role, spec in roles.items():
            if isinstance(spec, dict):
                models[role] = spec.get("model", "")
            else:
                models[role] = spec
        max_tokens = cfg["llm"].get("max_tokens", 65536)
        models["max_tokens"] = max_tokens
        cfg["models"] = models
    return cfg


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
        elif isinstance(v, list):
            out[k] = [_strip_none(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out


# ── public API ────────────────────────────────────────────────────────

def load() -> dict:
    """Load config from disk, auto-migrate legacy format, apply compat.

    Returns a dict with both new (llm.roles) and legacy (models) keys
    so all code paths work without changes.
    """
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                cfg = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as e:
            log.warning("Failed to parse %s: %s. Using defaults.", CONFIG_PATH, e)
            cfg = copy.deepcopy(DEFAULT_CONFIG)
    else:
        cfg = copy.deepcopy(DEFAULT_CONFIG)

    # Auto-migrate legacy format
    if _has_old_format(cfg):
        _backup()
        cfg = _migrate_from_old(cfg)
        try:
            with open(CONFIG_PATH, "wb") as f:
                tomli_w.dump(cfg, f)
            log.info("Migrated config saved to %s", CONFIG_PATH)
        except OSError as e:
            log.warning("Could not write migrated config: %s", e)

    # Apply compat + overrides
    cfg = _apply_backward_compat(cfg)
    cfg = _merge_defaults(cfg)
    cfg = _resolve_paths(cfg)
    cfg = _apply_env_overrides(cfg)
    cfg = _strip_none(cfg)
    return cfg


def init() -> dict:
    """Initialize config directory and file if missing, then load."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            with open(CONFIG_PATH, "wb") as f:
                tomli_w.dump(_strip_none(copy.deepcopy(DEFAULT_CONFIG)), f)
            log.info("Created default config at %s", CONFIG_PATH)
    except (PermissionError, OSError) as e:
        log.warning("Could not create config at %s: %s", CONFIG_PATH, e)
    return load()


def _backup():
    """Create a timestamped backup of the current config before migration."""
    if not CONFIG_PATH.exists():
        return
    try:
        shutil.copy2(str(CONFIG_PATH), str(CONFIG_BACKUP_PATH))
        log.info("Backed up existing config to %s", CONFIG_BACKUP_PATH)
    except OSError as e:
        log.warning("Could not create backup: %s", e)


def _merge_defaults(cfg: dict) -> dict:
    """Fill in missing config sections from DEFAULT_CONFIG.

    Ensures new sections (embedding, reranker, llm) are present
    even when loading a config file created by an older version.
    """
    for key, default_val in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = copy.deepcopy(default_val)
        elif isinstance(default_val, dict):
            for subkey, subval in default_val.items():
                if subkey not in cfg[key]:
                    cfg[key][subkey] = copy.deepcopy(subval)
    return cfg


# ── internal helpers (unchanged from before) ──────────────────────────

def _resolve_paths(cfg: dict) -> dict:
    workspace = cfg.get("workspace")
    if isinstance(workspace, dict):
        for key in ("path", "knowledge"):
            value = workspace.get(key)
            if not value:
                continue
            p = Path(value).expanduser()
            if not p.is_absolute():
                p = CONFIG_DIR / p
            workspace[key] = str(p.resolve())
    return cfg


def _apply_env_overrides(cfg: dict) -> dict:
    """Apply environment variable overrides to config."""
    telegram = cfg.get("telegram")
    if isinstance(telegram, dict):
        env_token = os.getenv("COZMO_TELEGRAM_BOT_TOKEN")
        if telegram.get("bot_token"):
            log.warning("telegram.bot_token found in config file; prefer the COZMO_TELEGRAM_BOT_TOKEN env var.")
        if env_token:
            telegram["bot_token"] = env_token

    # LLM default model
    env_val = os.getenv("COZMO_DEFAULT_MODEL")
    if env_val:
        cfg.setdefault("llm", {})
        cfg["llm"]["default_model"] = env_val

    # Per-role model overrides
    _ROLE_ENV_MAP = {
        "chat": "COZMO_MODEL_CHAT",
        "coder": "COZMO_MODEL_CODER",
        "vision": "COZMO_MODEL_VISION",
        "planner": "COZMO_MODEL_PLANNER",
        "classifier": "COZMO_MODEL_CLASSIFIER",
        "router": "COZMO_MODEL_ROUTER",
        "orchestrator": "COZMO_MODEL_ORCHESTRATOR",
    }
    llm = cfg.setdefault("llm", {})
    roles = llm.setdefault("roles", {})
    for role, env_key in _ROLE_ENV_MAP.items():
        env_val = os.getenv(env_key)
        if env_val:
            roles[role] = {"model": env_val}

    # Embedding model override
    env_val = os.getenv("COZMO_EMBED_MODEL")
    if env_val:
        cfg.setdefault("embedding", {})
        cfg["embedding"]["model"] = env_val

    # Reranker model override
    env_val = os.getenv("COZMO_RERANK_MODEL")
    if env_val:
        cfg.setdefault("reranker", {})
        cfg["reranker"]["model"] = env_val

    # Ollama URL override
    env_val = os.getenv("COZMO_OLLAMA_URL")
    if env_val:
        cfg.setdefault("ollama", {})
        cfg["ollama"]["url"] = env_val
        cfg.setdefault("providers", {})
        cfg["providers"].setdefault("ollama", {})
        cfg["providers"]["ollama"]["url"] = env_val

    return cfg
