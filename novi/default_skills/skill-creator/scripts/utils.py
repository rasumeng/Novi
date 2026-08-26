"""Shared utilities for skill-creator scripts."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError



def call_llm(prompt: str, model: str, ollama_url: str | None = None, timeout: int = 300) -> str:
    """Call an LLM via Ollama API or claude -p fallback.

    When ollama_url is set (e.g. http://localhost:11434), POST to /api/chat.
    Otherwise spawn ``claude -p`` subprocess (legacy Claude Code path).
    """
    if ollama_url:
        return _call_ollama(prompt, model, ollama_url, timeout)
    return _call_claude(prompt, model, timeout)


def _call_claude(prompt: str, model: str | None, timeout: int = 300) -> str:
    """Run ``claude -p`` with prompt on stdin."""
    cmd = ["claude", "-p", "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, env=env, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p exited {result.returncode}\nstderr: {result.stderr}")
    return result.stdout


def _call_ollama(prompt: str, model: str, ollama_url: str, timeout: int = 300) -> str:
    """POST prompt to Ollama /api/chat, return response text."""
    url = ollama_url.rstrip("/") + "/api/chat"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": 2048},
    }).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode())
        return data.get("message", {}).get("content", "")
    except URLError as e:
        raise RuntimeError(f"Ollama request failed: {e}")
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Ollama response parse failed: {e}")


def call_llm_structured(prompt: str, model: str, ollama_url: str | None = None, timeout: int = 300) -> dict | None:
    """Call LLM and parse response as JSON object.

    Wraps ``call_llm`` with a JSON extraction step.
    Returns parsed dict or None on failure.
    """
    raw = call_llm(prompt, model, ollama_url, timeout)
    # Try extracting JSON block
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_skill_md(skill_path: Path) -> tuple[str, str, str]:
    """Parse a SKILL.md file, returning (name, description, full_content)."""
    content = (skill_path / "SKILL.md").read_text()
    lines = content.split("\n")

    if lines[0].strip() != "---":
        raise ValueError("SKILL.md missing frontmatter (no opening ---)")

    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        raise ValueError("SKILL.md missing frontmatter (no closing ---)")

    name = ""
    description = ""
    frontmatter_lines = lines[1:end_idx]
    i = 0
    while i < len(frontmatter_lines):
        line = frontmatter_lines[i]
        if line.startswith("name:"):
            name = line[len("name:"):].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            value = line[len("description:"):].strip()
            # Handle YAML multiline indicators (>, |, >-, |-)
            if value in (">", "|", ">-", "|-"):
                continuation_lines: list[str] = []
                i += 1
                while i < len(frontmatter_lines) and (frontmatter_lines[i].startswith("  ") or frontmatter_lines[i].startswith("\t")):
                    continuation_lines.append(frontmatter_lines[i].strip())
                    i += 1
                description = " ".join(continuation_lines)
                continue
            else:
                description = value.strip('"').strip("'")
        i += 1

    return name, description, content
