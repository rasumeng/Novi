"""Bundled backend entry point for the Novi desktop application.

This module deliberately bypasses the developer-oriented CLI so a frozen
desktop sidecar has one small, stable responsibility: start the local WebUI
server requested by the Tauri shell.  It is packaged by
``scripts/build_desktop_backend.py`` for release builds.
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser("novi-backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    from novi.configuration.install import ensure_embedding_model
    from novi.ollama import is_ollama_running, start_ollama, stop_ollama, wait_for_ollama
    from novi.services import NoviContext
    from novi.webui_server import run_server

    ctx = NoviContext()
    ollama_url = ctx.config.get("ollama", {}).get("url", "http://localhost:11434")
    proc = None
    if not is_ollama_running():
        logging.getLogger("novi.desktop_backend").info("Starting Ollama")
        proc = start_ollama(ollama_url)
        if proc and not wait_for_ollama(ollama_url):
            logging.getLogger("novi.desktop_backend").warning("Ollama did not respond in time")

    ensure_embedding_model(ollama_url)
    try:
        run_server(ctx.config, host=args.host, port=args.port)
    finally:
        if proc:
            stop_ollama(proc)


if __name__ == "__main__":
    main()
