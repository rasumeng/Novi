# Brain V1 — Final Release Assessment

**Date:** 2026-08-05 · **Baseline:** 805/805 tests passing (~8.7s, no network deps).
**Prior audits:** `AUDIT-Brain-V1.md` (architecture/cognitive), `HARDENING-Brain-V1.md` (path wiring).
**Scope:** verdict + intentional debt + blockers. No code re-designed.

---

## 1. Is Brain V1 complete?

**Yes.** Feature-complete and architecturally frozen. Everything the audits
classified as a **HIGH wiring gap** has been closed in the working tree:

| Former HIGH gap | Status now |
|---|---|
| Tiered retrieval off by default | `tiered_resolver=True` default + in `context.py` wiring |
| Layered resolver not on the runtime read path | `MemoryRetrievalSource` consumes `Brain.recall` → layered resolver |
| `Brain.learn` / unified writer disconnected | `write_knowledge` → `brain.learn`; `search_memory` → `get_brain()` |

Phase F DoD items are verifiable in tests: consolidation, contradiction→edges,
decay (never delete), importance>recency, projection (no invented attributes),
inspect/correct, append-only, bounded reflection. 805 green confirms it.

## 2. Is the architecture stable?

**Yes.** Staleness is low and the boundaries are load-bearing *by test*:

- Layer boundaries enforced by `tests/test_architecture.py` (no storage imports
  above `brain/storage/`, no reasoning-tier I/O, no `metadata LIKE` filters).
- No circular imports; clean `Brain → Reasoning → Layers → Storage` direction.
- Append-only invariant respected throughout.
- The remaining change risk is concentrated in *Phase G deletions*, not in Brain
  behavior.

The Brain is safe to use as the foundation for the rest of Cozmo.

## 3. Intentional technical debt that remains (accepted for V1)

| Debt | Rationale for keeping |
|---|---|
| Flat `MemoryManager` + `brain=None` fallback + `get/set_memory_manager` global | Safety net while non-Brain entry points (CLI/WebUI/tools) still reference it; most are **bypasses** of Rule #6 worth fixing soon |
| Dead source adapters `sources/{identity,scenario,file}.py` (exported, never instantiated) | Unreachable; wiring them is Phase-E-natural but not required for the green path |
| Obsolete `brain/storage/migrations.py` (one-time Phase C→D adapter) | No runtime dependency; archive/delete is a trivial later change |
| Duplicated tier/durable-tag helpers across `projection/tiering/reflection/brain` | Behavior currently consistent; consolidation is a maintenance cleanup, not a correctness fix |
| Duplicated SQLite bootstrap ×3 scalar stores | Pure DRY, no behavior change, medium churn risk |
| Reflection triggers (scheduler `on_trigger` is a no-op) | Reflection runs manual-only for V1; silent self-maintenance is intentionally deferred |
| Scenario lifecycle completion (always `ACTIVE`) | Lifecycle field exists but is inert; completion detection deferred |

None of these blocks the merge. They are Phase G items (see `docs/ROADMAP-phaseG.md`).

## 4. Deferred to Phase G

Legacy removal, dead/obsolete code, technical-debt consolidation, and migration
completion — exactly as scoped in `docs/ROADMAP-phaseG.md`. **No new Brain
features** are planned.

## 5. Blockers before merging Brain V1 into main Cozmo

**None found.** Concretely verified:

- **781 committed baseline + working tree = 805 green**, no network, no external
  services. Merge cannot break the suite.
- No `TODO`/`FIXME` remain in `cozmo/brain/` (grep clean).
- Docs are restructured and cross-referenced (`docs/architecture/`, `docs/archive/`,
  `docs/audits/`, `DEVLOG` restored, `ROADMAP-phaseG.md`).

The only *prerequisite actions* are operational, not engineering: land the
uncommitted working-tree tail with clear commit boundaries (next section) and
push (`main` is 12 commits ahead of `origin/main`).

## 6. Recommended commit plan (for the working-tree tail)

The uncommitted work is the Phase F tail + hardening + docs + tests. Three
commits keep the green suite as one logical unit:

1. `brain: Phase F cognitive completion (consolidation, reflection, projection, tiering, trust surface)` — the `cozmo/brain/{brain,types,events,projection,reasoning/*,layers/knowledge,storage/*}` changes + their tests (`test_{reflection,decay,consolidation,evolution,projection,tiering,acceptance,brain,resolver,architecture}.py`).
2. `runtime: route retrieval/memory/tool paths through the Brain + inspection tool` — `runtime/*`, `runtime/sources/*`, `services/context.py`, `memory/manager.py`, `tools/*`, `webui_server.py` + retrieval/memory tests.
3. `docs: Brain V1 — docs restructure, DEVLOG restore, audits, Phase G roadmap` — `docs/**` moves + `DEVLOG.md` + new docs.

Details and a 44-file disposition are in the accompanying merge review.

---

## Verdict

Brain V1 is **complete, stable, tested, and merge-ready**. It is a defensible
permanent cognitive foundation: typed-column storage, append-only provenance,
clean layering, bounded reasoning, a derived trust surface, and 805 green tests.
Remaining work is Phase G cleanup, not Brain architecture. Development can shift
to the rest of Cozmo.