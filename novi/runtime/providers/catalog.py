"""Built-in MCP server catalog — curated list of known servers with default config.

Architecture:
  Catalog → User installs connector → Config → MCP Manager → Tool Registry → Runtime
"""

from dataclasses import dataclass, field


@dataclass
class EnvVar:
    key: str
    label: str
    secret: bool = False
    optional: bool = False
    default: str = ""


@dataclass
class CatalogEntry:
    id: str
    display_name: str
    description: str
    command: str
    args: list[str]
    transport: str = "stdio"
    tags: list[str] = field(default_factory=list)
    category: str = "Other"
    capabilities: list[str] = field(default_factory=list)  # e.g. ["files", "database"]
    env_vars: list[EnvVar] = field(default_factory=list)
    homepage: str = ""


_CATALOG: list[CatalogEntry] = [
    CatalogEntry(
        id="filesystem",
        display_name="Filesystem",
        description="Read, write, and search files on the local filesystem.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        transport="stdio",
        tags=["system", "file"],
        category="System",
        capabilities=["files"],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    ),
    CatalogEntry(
        id="git",
        display_name="Git",
        description="Read and analyze Git repositories — commits, branches, diffs, and history.",
        command="uvx",
        args=["mcp-server-git"],
        transport="stdio",
        tags=["development", "git", "vcs"],
        category="Development",
        capabilities=["git"],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    ),
    CatalogEntry(
        id="github",
        display_name="GitHub",
        description="Manage repositories, issues, pull requests, and more via the GitHub API.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        transport="stdio",
        tags=["development", "git", "api"],
        category="Development",
        capabilities=["github"],
        env_vars=[
            EnvVar(key="GITHUB_TOKEN", label="GitHub Personal Access Token", secret=True),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    ),
    CatalogEntry(
        id="puppeteer",
        display_name="Puppeteer",
        description="Headless browser automation — take screenshots, click elements, extract data.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-puppeteer"],
        transport="stdio",
        tags=["development", "browser", "automation"],
        category="Development",
        capabilities=["browser"],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
    ),
    CatalogEntry(
        id="playwright",
        display_name="Playwright",
        description="Browser automation with Playwright — navigate, click, scrape, and screenshot.",
        command="npx",
        args=["-y", "@anthropic/mcp-server-playwright"],
        transport="stdio",
        tags=["development", "browser", "automation", "testing"],
        category="Development",
        capabilities=["browser"],
        homepage="https://github.com/anthropics/anthropic-quickstarts/tree/main/mcp-server-playwright",
    ),
    CatalogEntry(
        id="sqlite",
        display_name="SQLite",
        description="Query and analyze SQLite databases — tables, indexes, and data.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sqlite", "{db_path}"],
        transport="stdio",
        tags=["data", "database", "sql"],
        category="Data",
        capabilities=["database"],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
    ),
    CatalogEntry(
        id="postgres",
        display_name="PostgreSQL",
        description="Query and explore PostgreSQL databases with read-only access.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres"],
        transport="stdio",
        tags=["data", "database", "sql"],
        category="Data",
        capabilities=["database"],
        env_vars=[
            EnvVar(key="DATABASE_URL", label="PostgreSQL connection string (postgresql://...)", secret=True),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
    ),
    CatalogEntry(
        id="memory",
        display_name="Memory",
        description="Persistent memory stored as a knowledge graph of entities and relations.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
        transport="stdio",
        tags=["ai", "memory", "knowledge-graph"],
        category="AI",
        capabilities=["memory"],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    ),
    CatalogEntry(
        id="sequential-thinking",
        display_name="Sequential Thinking",
        description="Multi-step reasoning tool — helps the model think through complex problems step by step.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        transport="stdio",
        tags=["ai", "reasoning"],
        category="AI",
        capabilities=["reasoning"],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/sequential-thinking",
    ),
    CatalogEntry(
        id="google-drive",
        display_name="Google Drive",
        description="List, read, search, and manage files on Google Drive.",
        command="npx",
        args=["-y", "@anthropic/mcp-server-google-drive"],
        transport="stdio",
        tags=["productivity", "cloud", "files"],
        category="Productivity",
        capabilities=["files", "cloud-storage"],
        env_vars=[
            EnvVar(key="GOOGLE_DRIVE_CREDENTIALS", label="Google Drive OAuth credentials JSON path", secret=False),
        ],
        homepage="https://github.com/anthropics/anthropic-quickstarts/tree/main/mcp-server-google-drive",
    ),
    CatalogEntry(
        id="google-calendar",
        display_name="Google Calendar",
        description="Read, list, search, and manage Google Calendar events and schedules.",
        command="npx",
        args=["-y", "@anthropic/mcp-server-google-calendar"],
        transport="stdio",
        tags=["productivity", "calendar", "scheduling"],
        category="Productivity",
        capabilities=["calendar"],
        env_vars=[
            EnvVar(key="GOOGLE_CALENDAR_CREDENTIALS", label="Google Calendar OAuth credentials JSON path", secret=False),
        ],
        homepage="https://github.com/anthropics/anthropic-quickstarts/tree/main/mcp-server-google-calendar",
    ),
    CatalogEntry(
        id="gmail",
        display_name="Gmail",
        description="Read, search, send, and manage Gmail messages and threads.",
        command="npx",
        args=["-y", "@anthropic/mcp-server-gmail"],
        transport="stdio",
        tags=["productivity", "email", "communication"],
        category="Productivity",
        capabilities=["email"],
        env_vars=[
            EnvVar(key="GMAIL_CREDENTIALS", label="Gmail OAuth credentials JSON path", secret=False),
        ],
        homepage="https://github.com/anthropics/anthropic-quickstarts/tree/main/mcp-server-gmail",
    ),
    CatalogEntry(
        id="slack",
        display_name="Slack",
        description="Search messages, list channels, and interact with Slack workspaces.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
        transport="stdio",
        tags=["communication", "chat"],
        category="Communication",
        capabilities=["communication"],
        env_vars=[
            EnvVar(key="SLACK_BOT_TOKEN", label="Slack Bot Token (xoxb-...)", secret=True),
            EnvVar(key="SLACK_TEAM_ID", label="Slack Team ID", secret=False),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    ),
    CatalogEntry(
        id="google-maps",
        display_name="Google Maps",
        description="Geocoding, place search, directions, and elevation data from Google Maps.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-google-maps"],
        transport="stdio",
        tags=["data", "maps", "geo"],
        category="Data",
        capabilities=["maps"],
        env_vars=[
            EnvVar(key="GOOGLE_MAPS_API_KEY", label="Google Maps API Key", secret=True),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/google-maps",
    ),
    CatalogEntry(
        id="brave-search",
        display_name="Brave Search",
        description="Web and local search via the Brave Search API.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-brave-search"],
        transport="stdio",
        tags=["search", "web"],
        category="Data",
        capabilities=["web-search"],
        env_vars=[
            EnvVar(key="BRAVE_API_KEY", label="Brave Search API Key", secret=True),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    ),
    CatalogEntry(
        id="sentry",
        display_name="Sentry",
        description="Query issues, events, and performance data from Sentry.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sentry"],
        transport="stdio",
        tags=["development", "monitoring", "errors"],
        category="Development",
        capabilities=["monitoring"],
        env_vars=[
            EnvVar(key="SENTRY_TOKEN", label="Sentry Auth Token", secret=True),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/sentry",
    ),
    CatalogEntry(
        id="everart",
        display_name="EverArt (Image Gen)",
        description="Generate images using AI models via the EverArt API.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-everart"],
        transport="stdio",
        tags=["ai", "image", "generation"],
        category="AI",
        capabilities=["image-generation"],
        env_vars=[
            EnvVar(key="EVERART_API_KEY", label="EverArt API Key", secret=True),
        ],
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/everart",
    ),
    CatalogEntry(
        id="cloudflare",
        display_name="Cloudflare API",
        description="Manage Cloudflare resources — DNS, Workers, KV, and more.",
        command="npx",
        args=["-y", "@cloudflare/mcp-server-cloudflare"],
        transport="stdio",
        tags=["development", "cloud", "infrastructure"],
        category="Development",
        capabilities=["infrastructure"],
        env_vars=[
            EnvVar(key="CLOUDFLARE_API_TOKEN", label="Cloudflare API Token", secret=True),
        ],
        homepage="https://github.com/cloudflare/mcp-server-cloudflare",
    ),
]


# Map of capability id → display label + icon
CAPABILITY_LABELS: dict[str, str] = {
    "files": "Files",
    "git": "Git",
    "github": "GitHub",
    "browser": "Browser Automation",
    "database": "Databases",
    "memory": "Long-term Memory",
    "reasoning": "Reasoning",
    "calendar": "Calendar",
    "email": "Email",
    "communication": "Communication",
    "maps": "Maps",
    "web-search": "Web Search",
    "monitoring": "Monitoring",
    "image-generation": "Image Generation",
    "infrastructure": "Infrastructure",
    "cloud-storage": "Cloud Storage",
}


def get_catalog() -> list[CatalogEntry]:
    return _CATALOG.copy()


def get_catalog_serializable() -> list[dict]:
    """Return catalog as plain dicts for JSON serialization."""
    return [
        {
            "id": e.id,
            "display_name": e.display_name,
            "description": e.description,
            "command": e.command,
            "args": e.args,
            "transport": e.transport,
            "tags": e.tags,
            "category": e.category,
            "capabilities": e.capabilities,
            "env_vars": [
                {
                    "key": ev.key,
                    "label": ev.label,
                    "secret": ev.secret,
                    "optional": ev.optional,
                    "default": ev.default,
                }
                for ev in e.env_vars
            ],
            "homepage": e.homepage,
        }
        for e in _CATALOG
    ]


def get_capability_labels() -> dict[str, str]:
    return dict(CAPABILITY_LABELS)


def lookup_by_name(name: str) -> dict | None:
    """Find catalog entry by display_name or id. Returns serializable dict or None."""
    for e in _CATALOG:
        if e.display_name == name or e.id == name:
            return {
                "id": e.id,
                "display_name": e.display_name,
                "description": e.description,
                "category": e.category,
                "capabilities": e.capabilities,
                "homepage": e.homepage,
                "transport": e.transport,
            }
    return None
