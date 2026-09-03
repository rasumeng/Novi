"""
Router benchmark — llama.cpp Qwen 0.5B GGUF, CPU-first.

Measures per spec §16:
  Routing quality: workload accuracy, relation accuracy, structured-output validity
  Performance: cold-start latency, warm latency, CPU %, RAM, model loading time
  Compares thread budgets 1/2/4

Usage:
  python -m novi.evaluation.router_bench --corpus tests/router_corpus.json [--model qwen2.5:0.5b] [--threads 1 2 4] [--save bench.json]
  python -m novi.evaluation.router_bench --corpus tests/router_corpus.json --mock   # headless mock (no llama.cpp)

If llama-cpp not installed or GGUF missing, falls back to mock LLM and reports as such.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from novi.orchestrator.router import WorkloadRouter, RouterState, RouterConfig


def _load_corpus(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class _MockLLM:
    """Deterministic mock that returns expected workload — for benchmark without model."""
    def __init__(self, corpus):
        self.by_query = {e["query"]: e for e in corpus}
    def invoke(self, prompt: str) -> str:
        marker = "Current user message (verbatim"
        idx = prompt.find(marker)
        tail = prompt[idx:] if idx != -1 else prompt
        for q, e in self.by_query.items():
            if q in tail:
                return json.dumps({
                    "workload": e["expected_workload"],
                    "confidence": 0.92,
                    "relation": e.get("expected_relation", "new"),
                    "state": e.get("prior_state", {"topic": "", "workload": e["expected_workload"], "status": "in_progress", "active_context": ""}),
                    "reasoning": "mock"
                })
        return json.dumps({"workload": "general", "confidence": 0.9, "relation": "new", "state": {"topic": "", "workload": "general", "status": "idle", "active_context": ""}, "reasoning": "fallback"})
    def warm(self): pass


def _mem_rss_mb() -> Optional[float]:
    try:
        import psutil, os
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except Exception:
        return None

def _cpu_percent(interval=0.2) -> Optional[float]:
    try:
        import psutil
        return psutil.cpu_percent(interval=interval)
    except Exception:
        return None


def benchmark_one(
    corpus: list[dict],
    model: str,
    router_config: RouterConfig,
    llm=None,
) -> dict:
    # Build router
    if llm is None:
        # Try llama.cpp if available and GGUF exists, else mock
        llm_to_use = None
        # If config has path, try LlamaCpp
        try:
            from novi.orchestrator.router import LlamaCppRouterLLM
            # Only attempt if GGUF path would resolve
            candidate = LlamaCppRouterLLM(config=router_config)
            # Check if model file exists — if not, fallback to mock
            mp = candidate._resolve_model_path()
            if Path(mp).exists():
                llm_to_use = candidate
        except Exception:
            llm_to_use = None
        if llm_to_use is None:
            llm_to_use = _MockLLM(corpus)
        llm = llm_to_use

    router = WorkloadRouter(model=model, llm=llm, router_config=router_config)

    # Cold start (load + first warm)
    t0 = time.perf_counter()
    load_ms = None
    try:
        router.warm()
        if hasattr(llm, 'get_stats'):
            stats = llm.get_stats()
            load_ms = stats.get('load_time_ms')
    except Exception:
        pass
    cold_ms = (time.perf_counter() - t0) * 1000

    mem_before = _mem_rss_mb()
    cpu_before = _cpu_percent(0.1)

    latencies: list[float] = []
    valid_json = 0
    correct_workload = 0
    correct_relation = 0
    total = 0

    for entry in corpus:
        prior = RouterState.from_dict(entry.get("prior_state"))
        if prior.is_empty():
            prior = None
        t = time.perf_counter()
        try:
            dec = router.route(
                user_message=entry["query"],
                state=prior,
                history=entry.get("history"),
                has_images=entry.get("has_images", False),
            )
            lat = (time.perf_counter() - t) * 1000
            latencies.append(lat)
            try:
                _ = dec.to_dict()
                # Validate schema: workload in 3, relation in 3
                if dec.workload in ("general", "research", "code") and dec.relation.value in ("new", "continue", "switch"):
                    valid_json += 1
            except Exception:
                pass
            if dec.workload == entry.get("expected_workload"):
                correct_workload += 1
            if dec.relation.value == entry.get("expected_relation", "new"):
                correct_relation += 1
            total += 1
        except Exception:
            latencies.append((time.perf_counter() - t) * 1000)
            total += 1

    lat_sorted = sorted(latencies)
    p50 = lat_sorted[len(lat_sorted)//2] if lat_sorted else 0
    p95 = lat_sorted[int(len(lat_sorted)*0.95)] if lat_sorted else 0
    avg = sum(latencies)/len(latencies) if latencies else 0

    # Warm latency = avg of last half (after warm)
    warm_lat = sum(latencies[len(latencies)//2:])/max(1, len(latencies)//2) if latencies else 0

    mem_after = _mem_rss_mb()
    cpu_after = _cpu_percent(0.1)

    backend = "mock"
    if hasattr(llm, 'get_stats'):
        try:
            s = llm.get_stats()
            backend = s.get('backend', 'llama.cpp')
        except Exception:
            pass
    elif llm.__class__.__name__ == "_MockLLM":
        backend = "mock"

    return {
        "model": model,
        "backend": backend,
        "n_threads": router_config.n_threads,
        "n_ctx": router_config.n_ctx,
        "n_predict": router_config.n_predict,
        "n_gpu_layers": router_config.n_gpu_layers,
        "cold_ms": round(cold_ms, 1),
        "load_ms": round(load_ms, 1) if load_ms else None,
        "warm_ms": round(warm_lat, 1),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "avg_ms": round(avg, 1),
        "valid_json_rate": round(valid_json / max(1, total), 3),
        "workload_accuracy": round(correct_workload / max(1, total), 3),
        "relation_accuracy": round(correct_relation / max(1, total), 3),
        "memory_rss_mb_before": round(mem_before, 1) if mem_before else None,
        "memory_rss_mb_after": round(mem_after, 1) if mem_after else None,
        "memory_delta_mb": round((mem_after - mem_before), 1) if mem_before and mem_after else None,
        "cpu_percent_before": cpu_before,
        "cpu_percent_after": cpu_after,
        "total": total,
        "router_stats": router.get_stats(),
    }


def benchmark(
    corpus_path: str = "tests/router_corpus.json",
    model: str = "qwen2.5:0.5b",
    runs: int = 1,
    llm=None,
    threads: list[int] | None = None,
    mock: bool = False,
) -> dict | list[dict]:
    corpus = _load_corpus(corpus_path)
    thread_list = threads or [1, 2, 4]
    results = []
    for t in thread_list:
        cfg = RouterConfig(n_ctx=2048, n_threads=t, n_predict=256, n_gpu_layers=0, temperature=0.0)
        llm_to_pass = llm
        if mock:
            llm_to_pass = _MockLLM(corpus)
        res = benchmark_one(corpus, model, cfg, llm=llm_to_pass)
        results.append(res)
    if len(results) == 1:
        return results[0]
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="tests/router_corpus.json")
    ap.add_argument("--model", default="qwen2.5:0.5b")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--threads", nargs="+", type=int, default=None, help="thread budgets to test, e.g. --threads 1 2 4")
    ap.add_argument("--mock", action="store_true", help="force mock LLM (no llama.cpp)")
    ap.add_argument("--save", default="")
    args = ap.parse_args()
    res = benchmark(args.corpus, args.model, args.runs, threads=args.threads, mock=args.mock)
    print(json.dumps(res, indent=2))
    if args.save:
        Path(args.save).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"saved to {args.save}")


if __name__ == "__main__":
    main()
