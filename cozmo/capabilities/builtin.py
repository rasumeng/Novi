"""
Built-in capability definitions (Python-based).

These will eventually migrate to TOML files in ~/.cozmo/capabilities/.
"""

from .base import Capability
from .registry import CapabilityRegistry


def register_builtin_capabilities(registry: CapabilityRegistry):
    """Register all built-in capabilities into the given registry."""

    # Create a weather tool to add into relavent registeries instead of utilizng a web search to find weather.
    registry.register(Capability(
        id="conversation",
        description="General conversation and Q&A",
        tools=["search_knowledge", "read_knowledge", "calculator", "current_time", "search_memory"],
        planner_strategy="none",
        risk="low",
    ))

    registry.register(Capability(
        id="search",
        description="Lightweight web search for current information",
        tools=["web_search", "web_fetch"],
        optional_tools=["fetch_url"],
        planner_strategy="none",
        risk="low",
    ))

    registry.register(Capability(
        id="research",
        description="Web research and information gathering",
        tools=["web_search", "web_search_pipeline", "web_fetch", "calculator", "search_knowledge"],
        optional_tools=["fetch_url", "read_knowledge"],
        planner_strategy="research",
        risk="low",
        template_patterns=[
            "search", "research", "find out", "what is", "news",
            "latest", "current", "weather", "price",
        ],
    ))

    registry.register(Capability(
        id="coding",
        description="Software development — read, write, edit, debug code",
        tools=["read_file", "write_file", "edit_file", "glob", "grep",
               "bash", "run_command", "list_directory"],
        optional_tools=["diagnostics", "execute_python", "git_diff", "git_log"],
        planner_strategy="coding",
        risk="medium",
        template_patterns=[
            "code", "implement", "fix", "refactor", "write",
            "build", "debug", "test", "function", "class",
        ],
    ))

    registry.register(Capability(
        id="planning",
        description="Strategic planning and architecture design",
        tools=["read_file", "list_directory", "grep", "search_knowledge",
               "read_knowledge", "calculator"],
        planner_strategy="planning",
        risk="low",
        template_patterns=[
            "plan", "architecture", "design", "spec", "proposal",
            "strategy", "roadmap",
        ],
    ))

    registry.register(Capability(
        id="vision",
        description="Image analysis and processing",
        tools=["analyze_image"],
        planner_strategy="none",
        risk="low",
    ))

    registry.register(Capability(
        id="memory",
        description="Read and write to long-term memory",
        tools=["search_knowledge", "read_knowledge", "write_knowledge",
               "search_memory", "current_time"],
        planner_strategy="none",
        risk="low",
    ))

    registry.register(Capability(
        id="filesystem",
        description="File system operations — read, write, list, search",
        tools=["read_file", "write_file", "edit_file", "list_directory",
               "glob", "grep"],
        planner_strategy="none",
        risk="medium",
        template_patterns=["read", "write", "list", "find", "search"],
    ))

    registry.register(Capability(
        id="terminal",
        description="Shell command execution",
        tools=["bash", "run_command", "execute_python"],
        planner_strategy="none",
        risk="high",
    ))
