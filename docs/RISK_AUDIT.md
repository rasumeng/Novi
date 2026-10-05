# Backend Risk Audit

An honest assessment of which parts of Novi to trust, in priority order for the
post-beta rework. Written by someone who spent a day reading this code and
running it — not a claim of full understanding.

Confidence labels:
**High** = understood, tested, and behaved as documented.
**Medium** = understood and working, but with sharp edges or wide blast radius.
**Low** = do not trust without reading it first.

---

## Distrust first

### 1. `novi/brain/` — Low confidence (dormant in beta)

36 modules, the largest package in the repo, built with substantial AI
assistance.

**Concrete evidence of a defect I could not explain.** During startup the Brain
opens its vector store and checks whether the stored vector width matches the
configured embedding dimension
(`brain/storage/vector_store.py:84-85`):

```python
if stored is not None and (stored != self._embed_dim or
                           (self._previous_space is not None and self._previous_space != self._space)):
    return self._rebuild_for_dimension(table, stored)
```

On a real profile this fired on **every boot**, copying the whole table and
re-embedding the entire corpus — 78 embedding calls, ~183 seconds. It left
behind **36 backup tables** named `knowledge_items_old_dim768_<stamp>`.

What I could not establish: why it fired. The stored width was 768, the
configured width was 768, and the recorded embedding-space file matched the
current space exactly. The log message only ever mentions dimensions — it
prints *"holds 39 vector(s) at dim 768 but the embedding dimension is now 768"*
— so it cannot be trusted to describe the condition that actually triggered it.
That second `or` branch (space mismatch) is the likely culprit, but I did not
prove it, and a rebuild can destroy data.

**Status:** gated off for beta (`brain.enabled = false`), which removes the cost
entirely — startup went from 194s to 8s. **Rework priority: highest.** Either
fix the trigger and make the log truthful, or delete the vector-store migration
path.

### 2. `novi/memory/` — Low confidence (dormant in beta)

Long-term recall and the knowledge index. Both retrieval tools bypassed the
`memory.enabled` gate entirely, which is how they ended up returning loosely
related memories (Reddit, DES encryption, linear algebra) for a query about the
weather. Fixed by gating, not by fixing retrieval.

**Status:** `memory.enabled = false`. Retrieval quality is unverified.

### 3. `novi/orchestrator/`, `novi/graphs/`, `novi/planner/` — Low confidence

19 modules that appear to predate `novi/runtime/`. There is at least one
explicit legacy shim (`NoviContext._legacy_create_runtime` raises
`RuntimeError("legacy runtime construction was removed; use run_service")`),
which implies the migration was partial.

**Action:** determine whether anything still reaches these. If not, delete them
before the rework so there is one obvious path.

### 4. Test-suite trustworthiness — Medium, with a caveat

2239 tests, all passing, and the count is *not* mostly padding: an AST audit
found **zero** tests whose assertions compare literals to literals. The 61
tests with no `assert` statement are architecture guard-rails that
`raise AssertionError` directly — they enforce real invariants (no hardcoded
model names, runtime must not touch storage internals, Brain is append-only).
Those are among the most valuable tests in the repo.

The real problem was **isolation, not quality**. `novi/paths.py` hardcodes
`HOME = Path.home() / ".novi"` with no environment override, so any test calling
`Configuration.set()` wrote into the developer's real profile. Observed damage
during ordinary test runs: `llm.primary_model` rewritten twice (once to a model
that isn't installed), the search backend flipped to Brave, `temperature`
changed, and a fake `GITHUB_TOKEN` injected.

**Fixed** with a session fixture that repoints the profile at a temp dir, plus
a `.gitignore` guard. **But the underlying hazard remains**: any new test that
touches real config can corrupt a user's install.

**Action:** when adding tests, never construct `NoviContext()` or `TestClient`
without the isolation fixture. If you find a test that writes to `~/.novi`, it
is a bug.

## Understand, but handle carefully

### `novi/webui_server.py` — Medium

The largest file, and it holds the request edge, attachment upload, persistence,
and every HTTP route. It also lazily constructs the backend, which is why the
first WebSocket connection used to pay the entire startup cost.

**Action:** when touching it, remember that module-level `CHATS_DIR`,
`ATTACHMENTS_DIR`, and `SKILLS_DIR` are bound at import time and need explicit
patching in tests.

### `runtime/agent_loop.py` — Medium, high value

The control flow for every run: limits, tool dispatch, cancellation, context
compaction, empty-turn recovery. Well covered by
`tests/test_agent_loop_protocol.py`, but it is dense and the interaction
between its counters (`empty_turn_retries`, `knowledge_search_count`,
`model_turns`) is subtle.

**Action:** keep the tests. This is the file most worth understanding deeply.

### `services/run_composition.py` — Medium

Where tool visibility and the system prompt are decided. Both are policy, and
both are currently hand-tuned strings.

**Action:** the prompt is the highest-leverage and least-tested surface in the
product. Changes to it need end-to-end verification against a real model, not
just unit tests — a prompt change can be "correct" and still make a small model
misfire.

### `llm.max_tokens` semantics — Medium

Passed straight through as `num_predict`. A value like 65536 permits very long
generations from a small model, which reads as a hang.

## Trust

- `runtime/run_contracts.py`, `runtime/transcript.py` — the vocabulary. Small,
  immutable, heavily used, well tested. **Start here.**
- `runtime/provider_adapter.py` — the LangChain boundary. The rule that model
  JSON in prose never becomes a tool call is enforced here and tested.
- `services/permission_service.py` — the authorization gate. Understandable and
  directly security-relevant.
- `services/run_transport.py` — pure event→JSON mapping, no logic.
- `tests/test_architecture.py` — the guard-rails. Keep them working; they are
  what stops the codebase drifting further.

## Open questions

Honest list of things I could not determine:

1. **Why does the Brain's dimension rebuild misfire?** Highest priority.
2. Is `orchestrator/` / `graphs/` / `planner/` reachable at all?
3. Does the reasoning-policy path behave correctly for providers other than
   Ollama? Only one provider is exercised in practice.
4. What is `evaluation/` for, and does anything gate releases with it?
5. Are the `test_m5x_*` / `test_m5xx_*` connector and MCP suites asserting
   current behaviour, or preserving behaviour nobody uses?

## A note on process

Much of this codebase was written with heavy AI assistance. That is not a defect
in itself — but it means **comments and docstrings describe intent, not
behaviour**, and the gap between the two is where the bugs live (the dimension
rebuild is exactly that: a log message that describes a condition the code does
not implement).

When you change something here, trust the tests and the observed behaviour over
the prose. And when the two disagree, that is worth a bug report — this document
exists to make those disagreements findable.
