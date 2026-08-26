"""Permission resolver with risk-aware pattern rules and session-level allowlists.

Resolution order:
  1. Session allowlist (user said "always allow" this session)
  2. Agent-specific config rules override
  3. Global config rules
  4. Tool risk level (fallback)
"""

from fnmatch import fnmatch

from .tool_risk import ToolRisk, get_tool_risk


def _input_key(tool: str, args: dict) -> str:
    """Map tool call to the string used for pattern matching."""
    if tool == "run_command":
        return args.get("command", tool)
    if tool in ("write_file", "edit_file", "read_file", "list_directory"):
        return args.get("path", tool)
    return tool


class PermissionResolver:
    def __init__(self, cfg: dict, auto: bool = False):
        self.cfg = cfg
        self.auto = auto
        self._session_allow: set[str] = set()

    def resolve(self, tool: str, args: dict, agent: str = "build") -> str:
        """Check permission. Returns 'allow', 'deny', or 'ask'.

        Resolution order:
          1. Session allowlist
          2. Agent config rules
          3. Global config rules
          4. Tool risk level (fallback)
        """
        key = _input_key(tool, args)

        # Session allowlist check first
        for pattern in self._session_allow:
            if fnmatch(key, pattern):
                return "allow"

        # Agent-specific overrides
        agent_cfg = self.cfg.get("agents", {}).get(agent, {})
        result = self._match(tool, key, agent_cfg.get("permissions", {}))
        if result:
            return result

        # Global permissions
        result = self._match(tool, key, self.cfg.get("permissions", {}))
        if result:
            return result

        # Fallback: tool risk level
        risk = get_tool_risk(tool)
        if risk == ToolRisk.LOW:
            return "allow"
        if risk == ToolRisk.CRITICAL:
            return "deny"
        return "ask"

    def _match(self, tool: str, key: str, rules: dict) -> str | None:
        if tool not in rules:
            return None
        rule = rules[tool]
        if isinstance(rule, str):
            return rule
        if isinstance(rule, dict):
            for pattern, action in reversed(list(rule.items())):
                if pattern == "*" or fnmatch(key, pattern):
                    return action
        return None

    def risk_of(self, tool: str) -> ToolRisk:
        """Return the risk level for a tool (agent config rules can override)."""
        key = tool
        agent_cfg = self.cfg.get("agents", {}).get("build", {})
        result = self._match(tool, key, agent_cfg.get("permissions", {}))
        if result == "deny":
            return ToolRisk.CRITICAL
        global_result = self._match(tool, key, self.cfg.get("permissions", {}))
        if global_result == "deny":
            return ToolRisk.CRITICAL
        return get_tool_risk(tool)

    def prompt(self, tool: str, args: dict, agent: str) -> bool:
        """Ask user. Returns True if allowed."""
        if self.auto:
            return True
        key = _input_key(tool, args)

        risk = self.risk_of(tool)
        prefix = ""
        if risk == ToolRisk.HIGH:
            prefix = "⚠️ HIGH RISK: "
        elif risk == ToolRisk.CRITICAL:
            print(f"\n🚫 [{agent}] {tool}: {key} — DENIED (critical risk)")
            return False

        while True:
            print(f"\n{prefix}⚠  [{agent}] {tool}: {key}")
            ans = input("Allow (o)nce / (a)lways / (d)eny: ").strip().lower()
            if ans in ("o", "once"):
                return True
            if ans in ("a", "always"):
                self._session_allow.add(key)
                return True
            if ans in ("d", "deny", "n", "no"):
                return False
            print("  o = once, a = always, d = deny")
