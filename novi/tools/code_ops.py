import subprocess
import re
import shutil
import time
from pathlib import Path
from . import register_tool
from .file_ops import allowed_root, resolve_in_workspace

@register_tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with content (workspace-confined)."""
    safe = resolve_in_workspace(path)
    if safe is None:
        return "Error: path outside allowed directory"
    try:
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Error writing file: {e}"
    return f"Written {len(content)} bytes to {path}"

@register_tool()
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in an existing file (first occurrence,
    workspace-confined)."""
    safe = resolve_in_workspace(path)
    if safe is None:
        return "Error: path outside allowed directory"
    try:
        content = safe.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"
    if old_text not in content:
        return f"Error: text not found in {path}"
    safe.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    return f"Replaced match in {path}"

@register_tool()
def grep_search(pattern: str, path: str = ".") -> str:
    """Regex search across files (Python fallback)."""
    root = Path(path)
    results = []
    for f in root.rglob("*"):
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line):
                    results.append(f"{f}:{i}: {line.strip()}")
        except Exception:
            pass
    return "\n".join(results[:200]) or "No matches found."

@register_tool()
def execute_python(code: str) -> str:
    """Execute Python code in a sandboxed environment and return stdout/stderr.

    Uses Docker if available (isolated, no network), falls back to a
    workspace-pinned subprocess.
    """
    docker_available = shutil.which("docker") is not None
    if docker_available:
        try:
            return _execute_in_docker(code)
        except subprocess.TimeoutExpired:
            return "[error] Code execution timed out (30s limit)"
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(allowed_root()),
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip() or "[no output]"
    except subprocess.TimeoutExpired:
        return "[error] Code execution timed out (30s limit)"
    except Exception as e:
        return f"[error] {e}"


def _execute_in_docker(code: str) -> str:
    dockerfile = Path(__file__).parent.parent / "docker" / "sandbox.Dockerfile"
    image_name = "novi-sandbox"
    if not _image_exists(image_name):
        build_result = subprocess.run(
            ["docker", "build", "-t", image_name, "-f", str(dockerfile), str(dockerfile.parent)],
            capture_output=True,
            timeout=60,
        )
        if build_result.returncode != 0:
            raise RuntimeError(f"Docker build failed:\n{build_result.stderr[:500]}")
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "256m",
            "--cpus", "1",
            "--read-only",
            "--tmpfs", "/tmp:size=50m",
            image_name,
            "python", "-c", code,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    return output.strip() or "[no output]"


def _image_exists(name: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", name],
        capture_output=True,
    )
    return result.returncode == 0


def _format_shell_output(result) -> str:
    """Human-readable shell output — legacy run_command shape preserved."""
    out = result.stdout
    if result.stderr:
        out += f"\nSTDERR:\n{result.stderr}"
    if len(out) > 10000:
        head = out[:2000]
        tail = out[-8000:]
        out = f"{head}\n... [{len(out) - 10000} chars truncated] ...\n{tail}"
    return out or "(no output)"


def _run_command_structured(command: str, timeout: int = 120):
    """Workspace-pinned shell execution with STRUCTURED results (Phase 8C).

    Returns a StructuredToolOutput: ``text`` keeps the exact legacy human
    formatting (existing consumers unchanged); ``data`` carries the machine
    contract — exit_code / stdout_tail / stderr_tail / duration_ms /
    timed_out / blocked — so verification never parses prose.
    """
    from ..runtime.tool_executor import StructuredToolOutput

    try:
        parts = _split_command(command)
    except ValueError:
        parts = command.split()

    blocked = {"rm", "del", "format", "shutdown", "reboot", "mkfs", "dd"}
    if parts and parts[0].lower() in blocked:
        return StructuredToolOutput(
            text=f"Error: command '{parts[0]}' is blocked for safety",
            data={"exit_code": None, "stdout_tail": "", "stderr_tail": "",
                  "duration_ms": 0.0, "timed_out": False, "blocked": True},
        )

    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(allowed_root()),
        )
    except subprocess.TimeoutExpired:
        return StructuredToolOutput(
            text=f"Error: command timed out after {timeout}s",
            data={"exit_code": None, "stdout_tail": "", "stderr_tail": "",
                  "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                  "timed_out": True, "blocked": False},
        )
    except FileNotFoundError as e:
        # Environment failure: the executable itself is missing. Report it
        # factually so verification can classify without guessing.
        return StructuredToolOutput(
            text=f"Error: {e}",
            data={"exit_code": None, "stdout_tail": "",
                  "stderr_tail": str(e)[:2000],
                  "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                  "timed_out": False, "blocked": False},
        )
    except Exception as e:
        return StructuredToolOutput(
            text=f"Error: {e}",
            data={"exit_code": None, "stdout_tail": "", "stderr_tail": "",
                  "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                  "timed_out": False, "blocked": False},
        )

    duration = round((time.perf_counter() - t0) * 1000, 2)
    text = _format_shell_output(result)
    data = {
        "exit_code": int(result.returncode),
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "duration_ms": duration,
        "timed_out": False,
        "blocked": False,
    }
    return StructuredToolOutput(text=text, data=data)


def _split_command(command: str):
    import shlex
    return shlex.split(command)


@register_tool()
def run_command(command: str):
    """Execute a shell command safely in the workspace root. Pipes and
    redirects allowed.

    Returns a StructuredToolOutput: ToolExecutor renders ``text`` for the
    model (legacy human formatting) and attaches ``data`` (exit code etc.)
    to ToolResult.structured for exact-semantics consumers.
    """
    return _run_command_structured(command)

@register_tool()
def git_diff() -> str:
    """Show unstaged git diff."""
    result = subprocess.run(["git", "diff"], capture_output=True, text=True, timeout=30)
    out = result.stdout or "(no unstaged changes)"
    if len(out) > 10000:
        head = out[:2000]
        tail = out[-8000:]
        out = f"{head}\n... [{len(out) - 10000} chars truncated] ...\n{tail}"
    return out


@register_tool()
def git_log(lines: int = 10) -> str:
    """Show recent commit history."""
    result = subprocess.run(["git", "log", f"-{lines}", "--oneline"], capture_output=True, text=True, timeout=30)
    return result.stdout or "(no commits)"