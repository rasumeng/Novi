import base64
from datetime import datetime
from pathlib import Path
import pyperclip
from PIL import ImageGrab
import requests

from . import register_tool

SCREENSHOT_DIR = Path.home() / ".cozmo" / "screenshots"


def _get_ollama_url() -> str:
    from .. import config
    cfg = config.load()
    return cfg.get("ollama", {}).get("url", "http://localhost:11434")


def _get_general_model() -> str:
    """Return the SELECTED general workload model, verbatim.

    Reads ``llm.workloads.general.model`` — the only model ever used for
    image analysis. There is no separate vision model and no fallback.
    """
    from .. import config
    cfg = config.load()
    llm = cfg.get("llm", {}) or {}
    workloads = llm.get("workloads", {}) or {}
    spec = workloads.get("general", "")
    if isinstance(spec, dict):
        return spec.get("model", "") or ""
    if isinstance(spec, str):
        return spec
    return ""


def _model_capabilities(model_name: str):
    from ..runtime.model_selector import model_capabilities
    return model_capabilities(model_name)


def _analyze_image(image_path: str, prompt: str = "Describe this image in detail.") -> str:
    model = _get_general_model()
    if not model:
        return ("Error: Model unavailable — no model selected for the 'general' "
                "workload (llm.workloads.general.model is unset). Select a model "
                "for General in the models page.")
    caps = _model_capabilities(model)
    if not caps.supports_vision:
        return (f"Error: Model '{model}' for workload 'general' does not support "
                f"image input. Select a vision-capable model for the general "
                f"workload.")
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        resp = requests.post(
            f"{_get_ollama_url()}/api/chat",
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }],
                "stream": False,
            },
        )
        if resp.status_code == 404:
            return (f"Error: Model unavailable — model '{model}' for workload "
                    f"'general' is not installed. Select an installed model in "
                    f"the models page.")
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "No description returned.")
    except Exception as e:
        return f"Error analyzing image: {e}"


@register_tool()
def screenshot(prompt: str = "Describe what's on this screen.") -> str:
    """Take a screenshot and analyze it. Optional: custom prompt for what to look for."""
    if not _is_desktop_enabled():
        return "Error: desktop tools disabled in config"
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
    img = ImageGrab.grab()
    img.save(path)
    return _analyze_image(str(path), prompt)


@register_tool()
def analyze_image(file_path: str, prompt: str = "Describe this image in detail.") -> str:
    """Analyze an existing image file. Provide file path and optional prompt."""
    p = Path(file_path)
    if not p.exists():
        return f"Error: file not found: {file_path}"
    return _analyze_image(file_path, prompt)


@register_tool()
def clipboard_read() -> str:
    """Read text from clipboard."""
    if not _is_desktop_enabled():
        return "Error: desktop tools disabled in config"
    try:
        return pyperclip.paste()
    except Exception as e:
        return f"Error reading clipboard: {e}"


def _is_desktop_enabled() -> bool:
    from .. import config
    cfg = config.load()
    return cfg.get("desktop", {}).get("enabled", False)
