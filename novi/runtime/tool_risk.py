"""Tool risk classification for smart permission gating.

Each tool has a risk level. Config rules still win, but risk provides
the DEFAULT behavior when no explicit rule exists.
"""

from enum import Enum


class ToolRisk(Enum):
    LOW = "low"           # Auto-allow: read-only, safe
    MEDIUM = "medium"     # Ask user: default for most tools
    HIGH = "high"         # Ask with warning: modifies state
    CRITICAL = "critical" # Deny unless explicit user override


# Default risk mapping for all known tools
# Tools not listed default to MEDIUM
_DEFAULT_RISK: dict[str, ToolRisk] = {
    # LOW — read-only, no side effects
    "read_file": ToolRisk.LOW,
    "list_directory": ToolRisk.LOW,
    "grep_search": ToolRisk.LOW,
    "web_search": ToolRisk.LOW,
    "web_search_pipeline": ToolRisk.LOW,
    "web_fetch": ToolRisk.LOW,
    "fetch_url": ToolRisk.LOW,
    "calculator": ToolRisk.LOW,
    "clipboard_read": ToolRisk.LOW,
    "current_time": ToolRisk.LOW,
    "diagnostics": ToolRisk.LOW,
    "get_public_ip": ToolRisk.LOW,
    "search_knowledge": ToolRisk.LOW,
    "read_knowledge": ToolRisk.LOW,
    "git_log": ToolRisk.LOW,
    "git_diff": ToolRisk.LOW,
    "git_status": ToolRisk.LOW,

    # MEDIUM — asks user, but not dangerous
    "search_memory": ToolRisk.MEDIUM,
    "python_repl": ToolRisk.MEDIUM,
    "run_python": ToolRisk.MEDIUM,
    "terminal_session": ToolRisk.MEDIUM,
    "list_skills": ToolRisk.MEDIUM,
    "load_skill": ToolRisk.MEDIUM,
    "describe_image": ToolRisk.MEDIUM,
    "ocr_image": ToolRisk.MEDIUM,
    "transcribe_audio": ToolRisk.MEDIUM,

    # HIGH — modifies files or system state
    "write_file": ToolRisk.HIGH,
    "edit_file": ToolRisk.HIGH,
    "write_knowledge": ToolRisk.HIGH,
    "git_commit": ToolRisk.HIGH,
    "convert_to": ToolRisk.HIGH,
    "create_project_file": ToolRisk.HIGH,
    "rename_file": ToolRisk.HIGH,
    "delete_file": ToolRisk.HIGH,
    "create_directory": ToolRisk.HIGH,

    # HIGH — runs commands
    "run_command": ToolRisk.HIGH,
    "run_powershell": ToolRisk.HIGH,
    "run_bash": ToolRisk.HIGH,
    "execute_shell": ToolRisk.HIGH,

    # CRITICAL — dangerous system operations
    "edit_crontab": ToolRisk.CRITICAL,
    "kill_process": ToolRisk.CRITICAL,
    "system_shutdown": ToolRisk.CRITICAL,
    "docker_exec": ToolRisk.CRITICAL,
    "sudo_command": ToolRisk.CRITICAL,
    "install_package": ToolRisk.HIGH,  # HIGH, not critical
    "pip_install": ToolRisk.HIGH,
}


def get_tool_risk(tool_name: str) -> ToolRisk:
    """Return the risk level for a tool. Defaults to MEDIUM for unknown tools."""
    return _DEFAULT_RISK.get(tool_name, ToolRisk.MEDIUM)


def risk_to_label(risk: ToolRisk) -> str:
    labels = {
        ToolRisk.LOW: "🟢 Low",
        ToolRisk.MEDIUM: "🟡 Medium",
        ToolRisk.HIGH: "🟠 High",
        ToolRisk.CRITICAL: "🔴 Critical",
    }
    return labels.get(risk, "🟡 Medium")
