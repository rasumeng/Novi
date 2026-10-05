import base64
import io
from datetime import datetime
from pathlib import Path
import pyperclip
from PIL import Image, ImageFilter, ImageGrab
import requests

from . import register_tool
from ..paths import home as app_home

SCREENSHOT_DIR = app_home() / "screenshots"


def _get_ollama_url() -> str:
    from ..configuration.bootstrap import get_configuration
    return get_configuration().get("ollama.url", "http://localhost:11434")


def _get_general_model() -> str:
    """Return the SELECTED primary model, verbatim. No fallback."""
    from ..configuration.bootstrap import get_configuration
    from ..configuration.resolver import get_primary_model
    try:
        return get_primary_model(configuration=get_configuration())
    except Exception:
        return ""


def _model_capabilities(model_name: str):
    from ..runtime.model_selector import model_capabilities
    return model_capabilities(model_name)


def _live_supports_vision(model_name: str) -> bool:
    """Ask the active Ollama runtime when local catalog metadata is stale."""
    try:
        resp = requests.post(
            f"{_get_ollama_url()}/api/show",
            json={"model": model_name},
            timeout=3,
        )
        resp.raise_for_status()
        capabilities = resp.json().get("capabilities", [])
        return isinstance(capabilities, list) and "vision" in capabilities
    except Exception:
        return False


def _prepare_image_bytes(image_path: str) -> bytes:
    """Enlarge tiny UI captures so local vision models receive legible text."""
    raw = Path(image_path).read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            longest = max(width, height)
            if longest >= 768 or longest <= 0:
                return raw
            scale = 1024 / longest
            resized = image.convert("RGB").resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            ).filter(ImageFilter.UnsharpMask(radius=1, percent=125, threshold=2))
            output = io.BytesIO()
            resized.save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return raw


def _analyze_image(image_path: str, prompt: str = "Describe this image in detail.") -> str:
    model = _get_general_model()
    if not model:
        return ("Error: Model unavailable — no primary model selected "
                "(llm.primary_model is unset). Select a model in the models page.")
    caps = _model_capabilities(model)
    if not caps.supports_vision and not _live_supports_vision(model):
        return ("The model you're currently using doesn't support image input. "
                "Choose a vision-capable model to analyze images.")
    try:
        b64 = base64.b64encode(_prepare_image_bytes(image_path)).decode()
        grounded_prompt = (
            f"{prompt}\n\nInspect the supplied image carefully. When reading text, "
            "transcribe only characters that are actually visible; preserve wording "
            "and punctuation, and say when any portion is genuinely illegible instead "
            "of guessing a language or inventing characters."
        )
        resp = requests.post(
            f"{_get_ollama_url()}/api/chat",
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": grounded_prompt,
                    "images": [b64],
                }],
                "stream": False,
            },
        )
        if resp.status_code == 404:
            return (f"Error: Model unavailable — model '{model}' "
                    f"is not installed. Select an installed model in "
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
    from ..configuration.bootstrap import get_configuration
    return get_configuration().get("desktop.enabled", False)
