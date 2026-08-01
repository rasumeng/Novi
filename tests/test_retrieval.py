"""Retrieval evaluation tests for KnowledgeIndex RAG pipeline.

Usage:
    python tests/test_retrieval.py          # Chunking unit tests only
    python tests/test_retrieval.py --eval   # Full retrieval eval (needs webui running)
"""

import sys
from pathlib import Path

ok = 0
fail = 0

def check(name, condition, detail=""):
    global ok, fail
    if condition:
        print(f"  OK   {name}")
        ok += 1
    else:
        print(f"  FAIL {name}: {detail}")
        fail += 1

def _run_chunk_tests():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cozmo.memory.knowledge_index import _chunk_with_overlap

    text = "Para one.\n\nPara two.\n\nPara three.\n\nPara four.\n\nPara five."

    chunks = _chunk_with_overlap(text, max_chars=20, overlap_chars=5)
    check("chunking produces >=2 chunks", len(chunks) >= 2,
          f"got {len(chunks)}: {chunks}")

    if len(chunks) >= 2:
        has_overlap = chunks[0][-5:] in chunks[1]
        check("chunks overlap", has_overlap,
              f"'{chunks[0][-5:]}' not in '{chunks[1][:20]}'")

    full_text = "\n\n".join(chunks)
    check("all content preserved", "Para one." in full_text and "Para five." in full_text)

    single = "Short text."
    result = _chunk_with_overlap(single)
    check("short text single chunk", len(result) == 1)

    empty = ""
    result = _chunk_with_overlap(empty)
    check("empty text returns []", result == [])

    print(f"\nChunking: {ok}/{ok+fail} passed, {fail} failed")
    return fail == 0

def _run_eval():
    import json
    import urllib.request

    BASE = "http://127.0.0.1:8080"
    def api(method, path, data=None):
        url = f"{BASE}{path}"
        try:
            if method == "GET":
                r = urllib.request.urlopen(f"{url}?q=test", timeout=5)
            else:
                req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                             headers={"Content-Type": "application/json"})
                r = urllib.request.urlopen(req, timeout=5)
            return r.status, json.loads(r.read())
        except Exception as e:
            return 500, str(e)

    status, result = api("GET", "/api/knowledge/search?q=python")
    check("knowledge search endpoint", status == 200 and result, str(result))

    if isinstance(result, list):
        for r in result:
            has_path = "path" in r.get("metadata", {}) or "path" in r
            check("result has path", has_path, str(r))
            break

    status, result = api("POST", "/api/knowledge/search", {"query": "async function", "k": 3})
    check("knowledge search POST", status == 200 and result, str(result))

    if isinstance(result, list):
        check("reranked results", len(result) <= 3, f"got {len(result)}")
        if result and "score" in result[0]:
            check("scores normalized 0-1", 0 <= result[0]["score"] <= 1,
                  f"score={result[0]['score']}")

    print(f"\nRetrieval eval: {ok}/{ok+fail} passed, {fail} failed")
    return fail == 0

if __name__ == "__main__":
    if "--eval" in sys.argv:
        _run_chunk_tests()
        _run_eval()
    else:
        print("=== Chunking unit tests ===")
        passed = _run_chunk_tests()
    sys.exit(0 if passed else 1)
