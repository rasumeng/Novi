"""LessonStore — lightweight reflection system for tool execution lessons.

Captures lessons from tool successes and failures, persists to JSON.
Injected into future system prompts as context.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("novi.lessons")

from ..paths import home as app_home

LESSONS_DIR = app_home() / "lessons"
LESSONS_PATH = LESSONS_DIR / "lessons.json"

_MAX_LESSONS = 20


class Lesson:
    """One learned lesson from tool execution."""

    def __init__(
        self,
        tool: str,
        pattern: str,
        insight: str,
        success: bool = True,
        count: int = 1,
        created: str = "",
    ):
        self.tool = tool
        self.pattern = pattern
        self.insight = insight
        self.success = success
        self.count = count
        self.created = created or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "pattern": self.pattern,
            "insight": self.insight,
            "success": self.success,
            "count": self.count,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        return cls(
            tool=d.get("tool", ""),
            pattern=d.get("pattern", ""),
            insight=d.get("insight", ""),
            success=d.get("success", True),
            count=d.get("count", 1),
            created=d.get("created", ""),
        )


class LessonStore:
    """Persistent store of tool execution lessons, injected into prompts."""

    def __init__(self, persist_dir: Optional[str] = None):
        self._lessons: list[Lesson] = []
        self._path = (Path(persist_dir) / "lessons.json") if persist_dir else LESSONS_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        path = self._path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text("utf-8"))
            self._lessons = [Lesson.from_dict(item) for item in data.get("lessons", [])]
        except Exception as e:
            log.warning("failed to load lessons: %s", e)

    def _save(self):
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"lessons": [l.to_dict() for l in self._lessons]}
        path.write_text(json.dumps(data, indent=2), "utf-8")

    def record(self, tool: str, args: dict, result: str):
        """Analyze a tool execution and record a lesson if instructive.

        Success lessons: patterns that work well.
        Error lessons: common mistakes to avoid.
        """
        is_error = result.startswith("Error:")
        pattern = self._extract_pattern(tool, args)

        if is_error:
            existing = self._find(tool, pattern, success=False)
            if existing:
                existing.count += 1
            else:
                self._lessons.append(Lesson(
                    tool=tool, pattern=pattern,
                    insight=f"Avoid: {result[:200]}",
                    success=False,
                ))
        elif "permission denied" in result.lower() or "denied" in result.lower():
            pass
        else:
            existing = self._find(tool, pattern, success=True)
            if existing:
                existing.count += 1
            else:
                self._lessons.append(Lesson(
                    tool=tool, pattern=pattern,
                    insight=f"Works: {result[:200]}",
                    success=True,
                ))

        self._trim()
        self._save()

    def _extract_pattern(self, tool: str, args: dict) -> str:
        """Extract a generalizable pattern from tool+args."""
        if tool in ("read_file", "write_file", "edit_file", "grep", "glob"):
            return f"{tool}: path pattern"
        if tool in ("bash", "run_command"):
            cmd = (args.get("command", "") or args.get("cmd", "") or "").split()[0] if args else ""
            return f"{tool}: {cmd}" if cmd else f"{tool}: generic"
        if tool in ("web_search", "web_fetch", "web_search_pipeline"):
            return f"{tool}: query pattern"
        if tool in ("calculator", "execute_python"):
            return f"{tool}: expression"
        return f"{tool}: args"

    def _find(self, tool: str, pattern: str, success: bool) -> Optional[Lesson]:
        for l in self._lessons:
            if l.tool == tool and l.pattern == pattern and l.success == success:
                return l
        return None

    def _trim(self):
        if len(self._lessons) > _MAX_LESSONS:
            self._lessons.sort(key=lambda l: l.count, reverse=True)
            self._lessons = self._lessons[:_MAX_LESSONS]

    def get_context(self, tool_names: Optional[list[str]] = None, max_lessons: int = 5) -> str:
        """Build a prompt context block from relevant lessons.

        Args:
            tool_names: Only include lessons for these tools (None = all).
            max_lessons: Max lessons to include.
        """
        if not self._lessons:
            return ""
        candidates = self._lessons
        if tool_names:
            candidates = [l for l in candidates if l.tool in tool_names]
        candidates.sort(key=lambda l: l.count, reverse=True)
        candidates = candidates[:max_lessons]

        lines = ["\n--- Lessons from past tool use ---"]
        for l in candidates:
            tag = "✅" if l.success else "❌"
            freq = f" (seen {l.count}x)" if l.count > 1 else ""
            lines.append(f"  {tag} {l.tool}: {l.insight[:150]}{freq}")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._lessons)

    def list_all(self) -> list[Lesson]:
        return list(self._lessons)

    def clear(self):
        self._lessons.clear()
        if self._path.exists():
            self._path.unlink()
