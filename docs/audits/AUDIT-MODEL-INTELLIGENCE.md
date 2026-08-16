# Repository-Wide Post-Phase-4 Architecture Audit — Model Intelligence & Discovery

**Date:** 2026-08-14
**Status:** Read-only audit. No production code changed.
**Audit type:** FACTS (discovered in the repository) vs RECOMMENDATIONS (future architecture). Clearly labeled throughout.

**Hard constraints treated as binding (from the task):**
1. No Automatic/Custom model mode. No `models.mode`, no automatic/custom semantics.
2. No authoritative hardcoded model universe. No production recommendation may depend on specific model names. No `if model == "..."` special-casing.
3. Workloads (`general`/`research`/`code`) are the only persisted model-selection concept.
4. Capabilities belong to models, not workloads. No separate Chat/Reasoning/Vision/Coder selection.
5. Recommendations never silently modify configuration. "Use Recommended" is the only apply path.
6. Runtime stays strict: workload → selected model → validate capabilities → execute. No ranking/fallback/substitution.

---

## 1. Executive summary

Cozmo has already completed the hard part of the migration. The **runtime is clean**: `ModelSelector` resolves the user's selected `llm.workloads.<workload>.model` verbatim with no ranking, fallback, or substitution (`cozmo/runtime/model_selector.py`, `cozmo/models/service.py`). Selection has exactly one write path (`apply_selection`, `cozmo/configuration/resolver.py:252`), triggered only by user action ("Use Recommended" or a direct selection write). The old `ModelRouter`, `models.mode`, `models.custom.assign`, roles, presets, and `llm.roles` are gone or reduced to one-way migration code (`cozmo/configuration/migration.py`).

What remains of the *old* architecture is concentrated in the **catalog/recommendation layer**:

- `cozmo/configuration/catalog.py` still holds the only model catalog — `KNOWN_MODEL_FACTS`, a **hardcoded, authoritative-looking list of 15 model names** with sizes, capabilities, qualification grades, and caveats. It is the sole input to recommendations, "available" install suggestions, and capability facts.
- `cozmo/configuration/eligibility.py`, `qualification.py`, and the discovery payload still carry **legacy "Automatic / Custom" vocabulary** (`eligible_automatic`, `eligible_custom`, `automatically_selectable`, `eligibleAutomatic`, `eligibleCustom`). None of it affects behavior anymore — it is emitted to the UI but never consumed — yet it contradicts the locked constraint #1.
- `cozmo/configuration/discovery.py` is genuinely dynamic for **installation state** (live Ollama `/api/tags`) but infers **capabilities from model-name substrings** (`llava`, `-vl`, `coder`, `codegemma`, `qwen2-vl`…) — name-based heuristics that the constraint forbids making authoritative.
- Model metadata captured locally is minimal: **name, size (bytes), provider, status**. No quantization, parameter count, context length, family, variant, source, format, license, or measured memory — and only one runtime (Ollama) is detected. Ollama's richer `/api/show` is never queried.
- There is **no remote/registry discovery** (no HF, Ollama registry, Unsloth, LM Studio) and **no caching** of any model metadata — every UI render live-queries Ollama and recomputes recommendations.
- The Model Library UI (`ModelsSettings.tsx`) is a **live inventory + static-catalog recommendation list**, not a discovery browser. There is no Explore/Search/details/variants/compatibility surface.

The system is therefore at the ideal seam: the runtime contract is locked and strict; the discovery + intelligence layer is what still leans on static, name-specific knowledge. The next phase should replace the static catalog with **dynamic model metadata** while keeping the strict runtime boundary untouched.

---

## 2. Current model architecture

### 2.1 The selection contract (FACT)

```
llm.workloads.<workload>.model   (persisted, TOML, user-authored)
   → ModelService.resolve(workload)          cozmo/models/service.py:45
   → ModelRegistry.validate(model_name)      cozmo/models/registry.py:36  (discovered set)
   → ModelSelector.resolve(workload)         cozmo/runtime/model_selector.py:80
   → provider.get_chat_model()               cozmo/providers/base.py
   → runtime execution                       cozmo/runtime/runtime.py:509-540
```

- Workloads are exactly `["general", "research", "code"]` — single source `WORKLOADS` (`cozmo/configuration/resolver.py:33`, duplicated in `cozmo/configuration/builtin.py:22`).
- Capability validation at runtime is **reject-only**: `model_capabilities(name)` is descriptive; image input requires `supports_vision` or the run fails with an explicit error (`cozmo/runtime/runtime.py:547-557`, `cozmo/runtime/model_selector.py:100-112`). Never a substitute.
- The `_workload_for` mapping (capability → workload, `cozmo/runtime/runtime.py:378-390`) decides *which workload slot* a task uses, not which model. This is the one place where task capability maps to workload — consistent with constraint #3 (no model named here).

### 2.2 The discovery/recommendation layer (FACT)

The WebUI path (`cozmo/webui_server.py`):

1. `GET /api/models/discovery` (`webui_server.py:1174`) →
2. `ModelDiscovery(url).installed()` live Ollama `/api/tags` (`cozmo/configuration/discovery.py:61`) →
3. `build_catalog_payload(installed)` (`cozmo/configuration/catalog.py:186`) → per model: `ModelRecommendationEngine.for_model` + `evaluate_eligibility` →
4. `build_available_recommendations(installed_names)` (`catalog.py:233`) — **iterates the static `KNOWN_MODEL_FACTS`** for "recommended but not installed" →
5. `recommend(installed=...)` (`cozmo/configuration/resolver.py:205`) — per-workload advisory, **iterates `KNOWN_MODEL_FACTS`** for ranking.

### 2.3 Who owns what (FACT)

| Concern | Owner | File |
|---|---|---|
| Persisted selection schema | runtime | `cozmo/configuration/builtin.py:32-62` |
| Selection write path | runtime | `cozmo/configuration/resolver.py:252` (`apply_selection`) |
| Provider abstraction + model listing | providers | `cozmo/providers/base.py` |
| Installed-model registry (in-memory) | models | `cozmo/models/registry.py` |
| Workload→model resolution | models/runtime | `cozmo/models/service.py`, `cozmo/runtime/model_selector.py` |
| Local inventory discovery | configuration | `cozmo/configuration/discovery.py` |
| Curated facts + recommendation engine | configuration | `cozmo/configuration/catalog.py` |
| Hardware detection | configuration | `cozmo/configuration/hardware.py` |
| Qualification/eligibility evidence | configuration | `cozmo/configuration/qualification.py`, `eligibility.py` |
| Install/pull | configuration | `cozmo/configuration/install.py` |
| Ollama process mgmt | root | `cozmo/ollama.py` |

---

## 3. Remaining hardcoded model knowledge

### 3.1 Authoritative hardcoded catalog (FACT — violation of constraint #2)

`cozmo/configuration/catalog.py:71-115` — `KNOWN_MODEL_FACTS`:

| Model name | display_name | approx_ram_gb | qualification | capabilities | Notes |
|---|---|---|---|---|---|
| `gemma4:e4b` | Gemma 4 E4B | 4.0 | TRUSTED | chat, reasoning, tools | |
| `qwen3:8b` | Qwen 3 8B | 8.0 | TRUSTED | chat, reasoning, coding, tools | |
| `qwen2.5vl:7b` | Qwen 2.5 VL 7B | 8.0 | TRUSTED | chat, vision, tools | |
| `gemma4` | Gemma 4 | 12.0 | TRUSTED | chat, reasoning, tools | `min_vram_gb=12.0`, caveat names `gemma4:e4b` |
| `phi3:mini` | Phi-3 Mini | 4.0 | SUPPORTED | – | |
| `llama3.2:3b` | Llama 3.2 3B | 4.0 | SUPPORTED | – | works_with_memory |
| `llama3.1:8b` | Llama 3.1 8B | 8.0 | SUPPORTED | chat, reasoning, tools | works_with_memory |
| `qwen2.5-coder:7b` | Qwen 2.5 Coder 7B | 8.0 | SUPPORTED | chat, coding, tools | |
| `qwen2.5-coder:32b` | Qwen 2.5 Coder 32B | 24.0 | SUPPORTED | chat, coding, tools | |
| `llama3.1:70b` | Llama 3.1 70B | 48.0 | SUPPORTED | chat, reasoning, tools | |
| `llava:7b` / `llava:13b` | LLaVA | 8.0 / 14.0 | SUPPORTED | chat, vision | |
| `nomic-embed-text` | Nomic Embed Text | 1.0 | SUPPORTED | embeddings | internal only |
| `mxbai-embed-large` | MixedBread Embed Large | 2.0 | SUPPORTED | embeddings | internal only |

These entries hardcode: names, display names, approximate RAM, qualification grades, capability lists, caveats, and a `min_vram_gb` hardware hint. This catalog is the *only* input to:
- `model_capabilities()` for installed models without a name match fallback (`cozmo/runtime/model_selector.py:45-71`),
- every recommendation reason (`catalog.py:124-180`),
- every "recommended but not installed" install suggestion (`catalog.py:233-280`),
- per-workload recommendation ranking (`cozmo/configuration/resolver.py:155-249`).

**FACT:** `tests/test_architecture.py:27-40` already encodes the policy — `HARDCODED_MODEL_PATTERNS` scans production code and whitelists `catalog.py` as the single permitted hardcode site. So today the repo *tolerates* exactly one catalog; it must become non-authoritative or dynamic to satisfy constraint #2.

### 3.2 Name-substring capability heuristics (FACT — name-based special-casing)

`cozmo/configuration/discovery.py:86-98` `_infer_capabilities`:

```python
if any(k in low for k in ("llava", "-vl", "vision", "minicpm", "qwen2-vl")):
    flags["vision"] = True
if any(k in low for k in ("coder", "codegemma")):
    flags["coding"] = True
```

This is model-family conditionals by another name. It defaults `tools: True` for everything. It is the fallback branch of `model_capabilities()` (`model_selector.py:64-71`) whenever a model is not in `KNOWN_MODEL_FACTS`.

### 3.3 Special-case caveat text naming a model (FACT)

`catalog.py:86-92`: the `gemma4` entry embeds a caveat string that says `gemma4:e4b is the recommended variant for lower-VRAM machines.` — a recommendation that depends on a specific model name, inside a hardcoded catalog entry.

### 3.4 Hardcoded embedding dimension (FACT)

`cozmo/services/embedding_providers.py:27`: `_FALLBACK_DIMENSION = 768  # nomic-embed-text`. The dimension is configurable (`embedding.dimension`, default 768) but the fallback is tied to a specific model by comment and value. `embedding.model` itself defaults to `""` (good, `bootstrap.py:34`), but vector-space compatibility is model-dependent and the rebuild tooling relies on the 768 assumption (`cozmo/memory/rebuild.py:4`).

### 3.5 Capability → hardware assumptions (FACT)

`cozmo/capabilities/builtin.py:61`: the `coding` capability declares `minimum_vram_gb=4.0`. `preferred_model_capability` values (`chat`, `research`, `coding`, `planning`, `vision`) exist on every capability (`builtin.py:19-112`) but are **never read** anywhere in the codebase (grep confirms definition + serialization only). They are dead metadata that mirrors the legacy role vocabulary.

### 3.6 Dev/utility hardcodes (FACT — not runtime)

- `cozmo/default_skills/skill-creator/scripts/run_eval.py:62` — `model or "qwen2.5-coder:7b"` (dev skill-eval tool, shells out to `claude -p`).
- `cozmo/providers/base.py:31,180-181` — docstring/comment examples (`'gpt-4o'`, `'qwen3:8b'`) only.
- `cozmo/configuration/manager.py:16`, `cozmo/runtime/execution_context.py:11` — docstring/comment examples only.
- `cozmo/services/embedding_providers.py:6,101`, `cozmo/memory/rebuild.py:4` — docstrings mentioning `nomic-embed-text`.

### 3.7 Legacy dead config keys in defaults (FACT)

`cozmo/configuration/bootstrap.py:29-89` `DEFAULT_CONFIG` still seeds: `router.use_llm`, `runtime.routing.intent_roles`, `runtime.routing.capability_roles`, `runtime.routing.capability_preferences`, `agents.build.model`, `agents.plan.model`. None are consumed by the runtime (grep confirms `routing.get("intent_capabilities", ...)` uses only `intent_capabilities`; roles/preferences are unread). They are migration/back-compat scaffolding that will keep confusing audits until removed.

**SUMMARY (FACT vs RECOMMENDATION):**
- **FACTS:** all locations above hardcode model knowledge.
- **Legitimate facts vs architectural hardcoding:** display names, approximate RAM, capability lists, and qualification *for a seed set* are legitimate *metadata/facts*. What is illegitimate is that they are (a) the **only** source, (b) treated as **authoritative** for recommendations/eligibility, and (c) keyed by exact model names so that any model not in the list is unqualified and un-recommendable.
- **RECOMMENDATION:** demote this list to *seed/overridable metadata* (one of several evidence sources), keyed by structured model identity (family/variant/size) instead of opaque names, and merged with locally-measured data from `/api/show`.

---

## 4. Existing discovery capabilities

### 4.1 `cozmo/configuration/discovery.py` (FACT)

- `ModelStatus` enum: `INSTALLED | AVAILABLE | MISSING`.
- `DiscoveredModel`: `name`, `provider` (default `ollama`), `status`, `size` (bytes), `capability_flags` (inferred).
- `ModelDiscovery`: `installed()`, `installed_names()`, `installed_map()`. Only calls Ollama `GET /api/tags`.
- **No** `/api/show` (no quantization/params/context/family), **no** `/api/ps` (load state), **no** remote registry query.

### 4.2 Provider-level listing (FACT)

- `OllamaProvider.list_models` (`cozmo/providers/base.py:115-124`): Ollama `/api/tags` → `ModelInfo(name, provider="ollama")`. Drops the size field.
- `OpenAIProvider.list_models` (`base.py:153-154`): returns `[ModelInfo(name=self.model_name, ...)]` — a single configured name, **not** a real inventory. There is no OpenAI model discovery.
- `PROVIDER_REGISTRY = {"ollama": ..., "openai": ...}` (`base.py:159-162`). Only two runtimes, one of which cannot enumerate.

### 4.3 What survives as generic infrastructure (RECOMMENDATION)

- The `DiscoveredModel` shape (name/provider/status/size/capabilities) is the right skeleton — it needs enrichment, not replacement.
- The `ModelDiscovery` seam (discovery object per runtime URL) generalizes cleanly to a **runtime inventory interface**.
- `ModelRegistry` (`cozmo/models/registry.py`) is a pure in-memory cache, provider-agnostic — survives as the cache tier.
- `hardware.py` is fully dynamic and model-agnostic — survives unchanged as the hardware-facts source for the recommendation layer.

### 4.4 What still assumes a static catalog (FACT)

- `catalog.py` `build_available_recommendations` — enumerates `KNOWN_MODEL_FACTS` to synthesize "available" install suggestions.
- `catalog.py` `ModelRecommendationEngine.for_model` — every reason string is derived from `KNOWN_MODEL_FACTS` fields.
- `resolver.py` `recommend()` / `_pick_workload_model` — rank ordering depends on `KNOWN_MODEL_FACTS` qualification + capability membership.
- `model_selector.py` `model_capabilities()` — curated branch is `KNOWN_MODEL_FACTS.get(name)`.
- `eligibility.py` — `fact = KNOWN_MODEL_FACTS.get(name)` by default; `qual = fact.qualification if fact else EXPERIMENTAL`.

So a model that is *installed and working* but absent from the 15-name list is treated as experimental, never recommended, and its capabilities are only guessed from its name.

---

## 5. Existing local-runtime inventory

### 5.1 Ollama (FACT)

Three independent Ollama touch points, none sharing a client:
1. `cozmo/ollama.py` — process lifecycle: `is_ollama_running()` (probe `/api/tags`), `start_ollama`/`stop_ollama`/`wait_for_ollama`.
2. `cozmo/providers/base.py:115` — `OllamaProvider.list_models` (`/api/tags`).
3. `cozmo/configuration/discovery.py:42` — `query_ollama_tags` (`/api/tags`) + `ModelDiscovery.installed()`.

**Metadata available locally today:** `name`, `size` (bytes). Everything else (capabilities) is inferred from the name. **Not used:** `/api/show` (model details incl. `details.parameter_size`, `details.quantization_level`, `model_info`), `/api/ps` (loaded models).

### 5.2 Other runtimes (FACT)

**None detected anywhere.** No LM Studio, no llama.cpp server, no MLC, no vLLM. `is_ollama_running`/`start_ollama` are the only runtime probes; the WebUI CLI start path (`cozmo/cli.py:314-336`) only ever launches `ollama serve`. OpenAI provider requires an API key and only "lists" the configured name. Nothing scans localhost ports or known sockets for alternative runtimes.

### 5.3 Gaps (RECOMMENDATION, not yet implemented)

- A runtime-inventory abstraction: probe which runtimes are reachable, then enumerate models per runtime.
- Enrich via Ollama `/api/show` for real facts: parameter size, quantization, context window, family/architecture, license fields when present.
- Read `ollama list`/config files as a fallback when the server is down (inventory without daemon).

---

## 6. Existing remote discovery capabilities

### 6.1 Present (FACT)

- **None for models.** No Hugging Face Hub, no Ollama registry/library search (`/api/library`), no Unsloth, no LM Studio catalog, no OpenAI model list API, no web catalog.
- The only external "search" code that exists is unrelated to models: SearXNG web search (`cozmo/tools/*`, `cozmo/searxng_util.py`) and Sourcegraph code search (`cozmo/tools/diagnostics.py:103`). No model registry is contacted.

### 6.2 Dependencies that could support remote discovery (FACT)

From `pyproject.toml:26-44`: `requests`, `httpx`, `langchain-openai`, `sentence-transformers`. No `huggingface_hub` installed. Ollama registry is reachable via plain HTTP (no extra dependency). `httpx`/`requests` are already available for any future registry client.

### 6.3 Position (RECOMMENDATION)

No external service is *required*. The Ollama library registry is an obvious first remote source (matches the only installed runtime, plain HTTP, no auth). HF Hub would require adding `huggingface_hub` or a plain HTTP client to `api.huggingface.co/api/models` (no SDK strictly necessary). Do not assume any service is mandatory — treat each source as an optional pluggable discovery provider.

---

## 7. Current recommendation pipeline

### 7.1 Flow (FACT)

```
GET /api/models/discovery                       webui_server.py:1174
  ├─ ModelDiscovery.installed()  ── live /api/tags
  ├─ build_catalog_payload()     catalog.py:186
  │    ├─ ModelRecommendationEngine.for_model   catalog.py:124
  │    │     ├─ hardware_fit_for(fact, hardware) eligibility.py:148
  │    │     └─ reasons from KNOWN_MODEL_FACTS fields
  │    └─ evaluate_eligibility()                eligibility.py:161
  ├─ build_available_recommendations()          catalog.py:233  ← iterates KNOWN_MODEL_FACTS
  ├─ recommend(installed) → Recommendations     resolver.py:205 ← iterates KNOWN_MODEL_FACTS
  ├─ payload["missingModels"], "workloads", "recommended", "vision_capable"
  └─ broadcast models_resolved                  webui_server.py:925 (advisory)
```

`POST /api/configuration/models/recommend` (`webui_server.py:1132`): recomputes advice; **only** when `{apply: true}` calls `apply_selection` — the sole write path. `POST /api/configuration/models/selection` (`webui_server.py:1110`) is the direct user write.

### 7.2 Where recommendation assumes specific models (FACT)

1. `resolver.py:_candidate_rank` (`:74-108`) — ranks by `fact.qualification`, `fact.min_vram_gb` vs VRAM, `fact.capabilities` membership, all from `KNOWN_MODEL_FACTS`; a model absent from the catalog is unrankable.
2. `resolver.py:_pick_workload_model` (`:155-202`) — reason strings embed catalog-derived vocabulary; `experimental / unverified (last resort)` for anything not TRUSTED/SUPPORTED.
3. `catalog.py:for_model` (`:124-180`) — reasons "Qualified: trusted", "Best for your hardware", "Works with Memory", "Supports Tool Calling" all come from catalog facts.
4. `catalog.py:build_available_recommendations` (`:233-280`) — literally loops the catalog names; excluded if `hardware_fit_for == DOES_NOT_FIT`; embedding-only models skipped by `USER_FACING_CAPABILITIES`.
5. `catalog.py:186-224` — payload entries with `eligibility.eligibleAutomatic/eligibleCustom` (legacy vocabulary; unused by the UI).

### 7.3 Persisted or derived? (FACT)

**Purely derived.** Recommendations are recomputed on every discovery call and after every install (`_after_models_changed`, `webui_server.py:912-933`). Nothing in `recommend()` reads or writes configuration (`resolver.py:205-249` is documented pure). The only persisted model-related values are: `llm.workloads.*` (selection), `embedding.model`/`embedding.backend`/`embedding.dimension`, `models.agent`, and `models.automatic.setup.dismissed` (UI dismissal state — see §12).

### 7.4 "Use Recommended" is the only intentional apply path (FACT — verified)

- `applyRecommendedModels` (frontend `api.ts:162`) → `POST .../recommend {apply:true}` → `apply_selection` (`resolver.py:252`).
- `apply_selection` writes exactly `llm.workloads.<workload>.model` verbatim; an uninstalled model is preserved and reported `not-installed`, never substituted (`resolver.py:268-284`).
- Install completion only refreshes *advisory* recommendations (`webui_server.py:1244` comment + `_after_models_changed`); selection is never rewritten.
- The `models_resolved` WS broadcast is informational; the frontend re-reads discovery and never auto-applies (`useFrameworkSettings.ts:66-69`).

---

## 8. Current model data model

### 8.1 The three model records (FACT)

| Type | Fields | Location |
|---|---|---|
| `ModelInfo` (provider) | `name`, `provider`, `tags` | `cozmo/providers/base.py:19-24` |
| `DiscoveredModel` (local inventory) | `name`, `provider`, `status`, `size`, `capability_flags` | `cozmo/configuration/discovery.py:24-39` |
| `ModelFact` (curated) | `name`, `display_name`, `approx_ram_gb`, `qualification`, `capabilities`, `caveats`, `vram_required_gb`, `min_vram_gb`, `works_with_memory`, `supports_tools`, `supports_vision` | `cozmo/configuration/catalog.py:26-65` |

### 8.2 Distinguishing coverage (FACT)

| Aspect | Present? | Where / gap |
|---|---|---|
| model family | **No** | only opaque `name` strings (`qwen3:8b`) |
| model variant | **No** | size is only implied by `approx_ram_gb` |
| runtime | Partial | `provider` = `ollama`/`openai` |
| source | **No** | no origin (registry, file, hf) tracked |
| format | **No** | no GGUF/safetensors awareness |
| quantization | **No** | not captured (`/api/show` never read) |
| parameter count | **No** | only approx RAM as a proxy |
| capabilities | Yes (partial) | catalog list OR name-inference; not measured |
| context length | **No** | absent everywhere |
| memory requirements | Partial | `approx_ram_gb`, `vram_required_gb` (always None), `min_vram_gb` hint |
| license | **No** | absent |
| installed/discovered state | Yes | `ModelStatus` |
| qualification evidence | Yes | `Qualification` grade |

### 8.3 Gap (RECOMMENDATION)

Adopt a single rich record (e.g. `ModelRecord`) that composes *identity* (family, variant, size-tier), *runtime/source/format* (where it came from), *measured facts* (from `/api/show`: params, quantization, context, license), *derived capabilities* (catalog + inference + measurement merged with provenance), and *state* (installed/available/missing). Fields should be optional-with-unknown (`None`) when not known — never fabricated, matching the `hardware.py` UNKNOWN discipline.

---

## 9. Current Model Library / UI

### 9.1 Structure (FACT)

`cozmo/webui/src/components/settings/ModelsSettings.tsx` renders three stacked sections:
1. **Recommended models** — advisory per-workload suggestions with a "Use Recommended" button (`:216-284`).
2. **Recommended model unavailable** — catalog-based "available" install cards with "Install & use" / "Not now" (`:288-368`).
3. **Current selection** — three workload `<select>` dropdowns over **installed** models (`:370-420`).
4. **Model library** — flat list of all entries, filter-by-name, install button, status badge (`:433-502`).

Data comes exclusively from `GET /api/models/discovery` (`api.ts:103`).

### 9.2 Is it a discovery browser or a static-catalog UI? (FACT)

**Closer to a live inventory + static-catalog recommendation list.** Entry set = installed models (Ollama) ∪ "recommended-available" models (static catalog) ∪ config-referenced missing models. Capabilities/qualification/caveats/reasons are catalog facts; only status/size are live. There is no remote search, no model detail view, no variants/quantization picker, no compatibility matrix, no pagination — just a name filter and an install button.

### 9.3 What must change for the target surface (RECOMMENDATION)

- **Installed** tab: live inventory (already exists, needs enrichment: quant, params, context, size shown).
- **Recommended** tab: advisory cards (exists; needs de-cataloging of reasons — derive from generic capability/evidence, not name-keyed facts).
- **Explore/Search** tab: remote-registry search (Ollama library, HF) — new.
- **Model details**: family/variant/runtime/source/format/quant/params/context/memory/license + capability evidence with provenance — new.
- **Compatibility**: hardware-fit + qualification + capability-match surface derived from generic data — new.
- **Variants**: same family, different quant/size — needs structured identity (currently impossible, names are opaque).

Also: the payload currently ships unused `eligibility.eligibleAutomatic/eligibleCustom` (`api.ts:43-48`) which must be removed per constraint #1.

---

## 10. Installation / acquisition architecture

### 10.1 The five concepts and where they live (FACT)

| Concept | Location | Mechanism |
|---|---|---|
| 1. Discovery | `cozmo/configuration/discovery.py`, `providers/base.py:list_models` | Ollama `/api/tags` |
| 2. Recommendation | `resolver.py`, `catalog.py` | static-catalog ranking |
| 3. Selection | `apply_selection` + `builtin.py` settings | `llm.workloads.*` |
| 4. Acquisition | `cozmo/configuration/install.py` | Ollama `/api/pull` (streamed, threaded) |
| 5. Execution | `providers/base.py` | ChatModel per provider |

### 10.2 Coupling (FACT)

- Discovery and acquisition are **both hardcoded to Ollama HTTP** (`/api/tags`, `/api/pull`) — the provider abstraction (`list_models`) is generic but `ModelInstaller` is not provider-abstracted at all.
- Recommendation and acquisition are joined only in the WebUI: the "available" install suggestions are catalog models (`build_available_recommendations`), and installing them goes straight to `ModelInstaller.pull`. There is no "resolve which runtime/source supplies this model" step.
- Install in-flight dedup is a WebUI-level `_installing_models` set (`webui_server.py:893,1225-1247`), not a service.
- Embeddings acquisition (`embedding.model` via `embedding_providers.py`) is separate and also Ollama-specific for the default backend.

### 10.3 Recommendation

Model acquisition should be driven by a discovered **source** on the model record (which runtime, which registry/URL), so install routes through the right acquirer (Ollama pull today; future: LM Studio CLI, HF download). Keep the five concerns distinct — the current split is already close, but discovery/acquisition need to become runtime-abstracted rather than Ollama-typed.

---

## 11. Offline / cache capabilities

### 11.1 Current state (FACT)

- **No caching of model metadata.** Every `GET /api/models/discovery` live-queries Ollama and recomputes recommendations. Every `llm.*` config change triggers `ModelService.refresh()` (`webui.py:140-146`), which clears the registry and re-queries all providers (`service.py:75-89`). `_after_models_changed` re-runs discovery after every install.
- `ModelRegistry` (`cozmo/models/registry.py`) is an in-memory cache for *discovered instances*, not persisted metadata. `ModelService._provider_cache` caches provider objects by model name (`service.py:41,147-163`).
- No `@lru_cache`, no TTL, no disk cache anywhere for model data.

### 11.2 Reusable infrastructure (FACT)

- Config framework TOML persistence (`store.py`) — could persist a metadata cache file.
- `retrieval_coordinator.seed_cache` (`runtime/retrieval_coordinator.py:184`) — demonstrates an in-memory bounded cache pattern for results (not model metadata).
- LanceDB / sqlite stores exist but are memory/knowledge-specific.

### 11.3 Recommendation (not implemented)

A metadata cache layer (in-memory with TTL + optional disk) keyed by runtime identity, invalidated on install/uninstall/config change. Should never mask a live daemon that is actually reachable — i.e. serve from cache only to avoid repeated `/api/tags`/`/api/show` hammering, with a documented staleness bound.

---

## 12. Remaining legacy architecture

### 12.1 Automatic/Custom residue — CONTRADICTS constraint #1 (FACT)

| Location | Residue |
|---|---|
| `cozmo/configuration/eligibility.py:85-101,189-248` | `ModelEligibility.eligible_automatic` / `eligible_custom` fields + logic; serialized as `eligibleAutomatic`/`eligibleCustom` |
| `cozmo/configuration/qualification.py:41-48` | `Qualification.automatically_selectable` property ("proactive Automatic selection") |
| `cozmo/configuration/catalog.py:214-219` | discovery payload still emits `eligibleAutomatic`/`eligibleCustom` |
| `cozmo/configuration/schema.py:17` | `Category.MODELS` description: "Automatic / Custom model configuration" |
| `cozmo/configuration/migration.py:18,111-115` | preserves `models.automatic.setup.dismissed` (recommendation-dismissal UI state) |
| `cozmo/configuration/resolver.py:9` header | "future Automatic resolution layer" phrasing |
| `cozmo/webui/src/components/settings/AgentSettings.tsx:113` | `{agentModel || 'Automatic'}` label |
| `cozmo/webui/src/components/settings/api.ts:43-48` | `ModelEligibility` type with automatic/custom fields |

None of these affect runtime behavior today — they are dead vocabulary in payloads/properties/docs. But they must be renamed/removed in Phase 5 to honor constraint #1. Note the *persisted* dismissal state (`models.automatic.setup.dismissed`) is functional (suppresses the setup card); only its key name carries the forbidden word.

### 12.2 Dead legacy code (FACT)

- `cozmo/webui_server.py:469-482` `apply_agent_config()` — **defined but never called**; the WS `agent_config` handler (`:1820-1822`) only stores and echoes. The `force_model` override it would apply is therefore inert.
- `cozmo/runtime/runtime.py:179,193,612,908` — `model_manager` parameter still accepted/fallback (`model_service or model_manager`); always `None` from `services/context.py:381`.
- `cozmo/configuration/bootstrap.py:42,60-77,79-84` — `router.*`, `runtime.routing.intent_roles/capability_roles/capability_preferences`, `agents.{build,plan}.model` dead keys.
- `cozmo/capabilities/builtin.py` `preferred_model_capability` (all occurrences) — defined, never read.
- `cozmo/configuration/catalog.py:182` `ModelRecommendationEngine.recommend_all` — defined, unused (payload uses `for_model`).
- `cozmo/configuration/discovery.py:82` `_parse_capabilities` — trivial alias of `_infer_capabilities`, unused.
- `cozmo/configuration/eligibility.py:256` `evaluate_all` — defined, unused by production (payload calls `evaluate_eligibility` per entry).
- `cozmo/models/registry.py` fine; `ModelService.bind_model` (`service.py:54`) appears unused (runtime binds via `provider.bind_tools` on the resolved client).

### 12.3 Docs that describe the old architecture (FACT)

- `docs/architecture/configuration-framework.md` has a "Superseded in part" banner but body still describes `llm.roles`, presets, `ModelRouter` (its own §6 says to remove them).
- `PLAN.md` §2.10, §5 (Phase 10), §7 still describe `ModelRouter` / resource-aware routing; `PLAN.md:7` "the user never chooses a mode" is the old phrasing.
- `docs/architecture/phaseF-design.md` — review for model-routing content when the design doc is next revised.

---

## 13. Architectural gaps for Model Intelligence

1. **No dynamic model metadata.** Installed models carry only `name`+`size`. Quantization, params, context, family, variant, license, measured memory are absent despite Ollama exposing them via `/api/show`. (RECOMMENDATION)
2. **Capabilities are name-guessed, not derived.** `_infer_capabilities` keys off substrings (`llava`, `-vl`, `coder`, `codegemma`, `qwen2-vl`); anything else defaults to `tools:true` only. No measured capability evidence, no provenance model. (FACT→RECOMMENDATION)
3. **Single hardcoded catalog is the only intelligence source.** `KNOWN_MODEL_FACTS` gates recommendation, eligibility, and capability facts. Constraint #2 requires replacing it with multi-source, structured, non-name-keyed metadata. (FACT→RECOMMENDATION)
4. **Recommendation is model-name-keyed and exhaustive-list based.** `recommend()` and `build_available_recommendations` only consider catalog members. A generic capability/evidence-based recommender is required. (FACT→RECOMMENDATION)
5. **No remote discovery.** Ollama library registry, HF, LM Studio catalogs: all absent. (FACT)
6. **No runtime abstraction for inventory.** Only `ollama`/`openai`; only Ollama enumerates; no port/socket probing; no LM Studio. (FACT→RECOMMENDATION)
7. **No metadata caching.** Live query on every render + refresh. (FACT→RECOMMENDATION)
8. **Model identity is a flat string.** Cannot distinguish family/variant/quant/source — blocks Explore, variants, compatibility, and correct install routing. (FACT→RECOMMENDATION)
9. **Automatic/Custom vocabulary remains** in eligibility/payload/UI/schema. (FACT — constraint violation to remove)
10. **Install is Ollama-only and not source-driven.** (FACT→RECOMMENDATION)
11. **No offline inventory when the daemon is down.** `ModelDiscovery.installed()` returns `[]` on any error; config-referenced models appear "missing". (FACT→RECOMMENDATION)
12. **Dead agent-model override path.** `models.agent` is registered/preserved/displayed but has no effect; `apply_agent_config` never runs. Either remove or make it a real, explicit selection surface (never an implicit override). (FACT→RECOMMENDATION)

---

## 14. Recommended Phase 5 architecture

Scope: **Model Intelligence & Discovery.** Builds dynamic model metadata while keeping every locked constraint.

### 14.1 Core principle

**The runtime stays exactly as-is.** `workload → selected model → validate → execute` is untouched. Phase 5 replaces only the *knowledge* layer that feeds discovery, the library UI, and recommendations.

### 14.2 Proposed components

1. **Structured model identity** — parse/normalize a model identifier into `{family, variant, size_tier, quant, runtime}` with unknown-safe fields (`None` when unknown). No production logic keys off family names; identity is only a *classification* surface.
2. **`ModelRecord`** — the single rich record: identity + runtime/source/format + measured facts (`/api/show` for Ollama) + derived capabilities (catalog seed + inference + measurement, each with `source` provenance) + state. All optional fields `None` when unknown (mirror `hardware.py` UNKNOWN discipline).
3. **Runtime inventory providers** — one interface (`list_models() -> list[ModelRecord]`, plus `show(name)`), implemented for Ollama now, extensible later. OpenAI returns its configured models as explicit selections (no fake enumeration).
4. **Discovery service** — merges runtime inventory + config-referenced (missing) + remote sources (optional, pluggable) into a unified inventory. Adds an optional metadata cache (in-memory TTL + disk).
5. **Evidence/capability layer** — capability facts assembled with provenance: curated seed (demoted from authoritative) + name-inference (kept as *weakest* evidence, explicitly labeled) + measured (`/api/show` `capabilities` when the runtime reports them). No capability result is ever fabricated.
6. **Generic recommendation engine** — recommendations computed from *structured predicates* (capability coverage, qualification evidence, numeric hardware-fit with unknown-safe logic), never from `if model == ...`. Catalog facts become one evidence source, merged and annotated, not a gate.
7. **De-credentialing the catalog** — `KNOWN_MODEL_FACTS` becomes seed data (e.g. `model_seeds.py` or a JSON asset) loaded into the evidence layer, never imported as the sole authority by eligibility/recommendation/capability code.
8. **Automatic/Custom residue removal** — delete `eligible_automatic/eligible_custom` fields and payload keys, `automatically_selectable`, the `Category.MODELS` docstring, the `'Automatic'` UI label; rename `models.automatic.setup.dismissed` → `models.recommendations.dismissed` with migration. Keep the dismissal *behavior*.
9. **UI as discovery browser** — tabs: Installed (rich details), Recommended (advisory, provenance-labeled), Explore/Search (remote sources), Model details drawer, compatibility panel. Workload selection UI unchanged (dropdown over installed models; "Use Recommended" stays the only apply path).

### 14.3 Non-goals (locked)

- No `models.mode`, no Automatic/Custom mode, no auto-apply of recommendations.
- No hardcoded model universe; no model-name conditionals in production logic.
- No separate Chat/Reasoning/Vision/Coder selections; no new persisted concepts beyond `llm.workloads.*`.
- No runtime ranking/fallback/substitution.
- No new external service required; remote sources are optional plug-ins.

---

## 15. Proposed phases after Phase 5

1. **Phase 5 — Dynamic Model Metadata & Discovery Service** (identity, ModelRecord, Ollama `/api/show`, runtime-inventory interface, metadata cache, catalog de-credentialing, Automatic/Custom residue removal).
2. **Phase 5.5 — Generic Recommendation & Evidence** (predicate-based recommender, capability provenance, hardware-fit generalization, seed data extraction).
3. **Phase 6 — Remote Discovery & Explore** (Ollama library registry source; optional HF source; Explore/Search UI tab).
4. **Phase 6.5 — Model Acquisition Abstraction** (source-driven install routing; multi-runtime acquirers; offline inventory from config files when daemon is down).
5. **Phase 7 — Model Library browser completion** (details drawer, variants/quantization picker, compatibility matrix, measured-state display).
6. **Phase 8 — Evaluation of Model Intelligence** (recommendation-quality benchmarks; capability-inference accuracy corpus; discovery latency/coverage metrics), aligned with the existing Phase-8 evaluation framework.

---

## 16. Exact files likely to change

| File | Change type | Phase |
|---|---|---|
| `cozmo/configuration/catalog.py` | Rewrite: ModelFact → evidence seeds; payload drops automatic/custom | 5 / 5.5 |
| `cozmo/configuration/discovery.py` | Rewrite: runtime-inventory providers + `/api/show` + cache seam | 5 |
| `cozmo/configuration/eligibility.py` | Remove automatic/custom; align to ModelRecord | 5 |
| `cozmo/configuration/qualification.py` | Remove `automatically_selectable`; reframe wording | 5 |
| `cozmo/configuration/resolver.py` | Generalize `recommend()`/`apply_selection` to generic evidence | 5.5 |
| `cozmo/configuration/hardware.py` | Unchanged (survives) | – |
| `cozmo/configuration/install.py` | Abstract acquirer interface | 6.5 |
| `cozmo/configuration/schema.py` | Category.MODELS description; dismissal key rename | 5 |
| `cozmo/configuration/migration.py` | Rename dismissal key; drop dead back-compat | 5 |
| `cozmo/configuration/bootstrap.py` | Remove dead `router.*`, `routing.*roles*`, `agents.*model` defaults | 5 |
| `cozmo/providers/base.py` | `list_models` → richer ModelRecord; show() seam | 5 |
| `cozmo/models/registry.py` | Enrich cache to ModelRecord; optional persistence | 5 |
| `cozmo/models/service.py` | Consume richer records; drop unused `bind_model` | 5 |
| `cozmo/runtime/model_selector.py` | `model_capabilities` → evidence-service (provenance) | 5.5 |
| `cozmo/runtime/runtime.py` | Remove `model_manager` fallback | 5 |
| `cozmo/capabilities/builtin.py` | Remove dead `preferred_model_capability`, re-home `minimum_vram_gb` | 5 |
| `cozmo/services/embedding_providers.py` | De-nomic the fallback dimension / docstrings | 5 |
| `cozmo/services/context.py` | Wire discovery service; drop model_manager | 5 |
| `cozmo/webui_server.py` | Discovery endpoints → service; remove dead `apply_agent_config`; rename dismissal endpoint | 5 / 6 |
| `cozmo/webui/src/components/settings/ModelsSettings.tsx` | Tabs + details drawer | 6 / 7 |
| `cozmo/webui/src/components/settings/api.ts` | Payload types: drop automatic/custom; add record fields | 5 |
| `cozmo/webui/src/components/settings/AgentSettings.tsx` | Remove `'Automatic'` fallback label | 5 |
| `tests/test_architecture.py` | Extend model-name guard; whitelist seed data path | 5 |
| `docs/architecture/configuration-framework.md`, `PLAN.md` | Refresh to workload + Model Intelligence architecture | 5 |

---

## 17. Risks / dependencies

1. **Recommendation quality regression** while de-cataloging. Mitigate with evidence-provenance labeling and evaluation (Phase 8 framework already exists).
2. **`/api/show` dependence** — Ollama version variance in the show payload; parse defensively, keep fields optional/unknown. No hard requirement that it succeed.
3. **Naming/terminology sweep** is subtle — the word "automatic" also appears in unrelated contexts (e.g. `permissions.write_file` "Never ask", `DetectionConfidence`, WS permission mode). Sweep must be model-scoped only.
4. **Cache staleness** vs live daemon. Bounded TTL + explicit invalidation on install/uninstall/config change; never serve stale data as "installed" for runtime validation (runtime registry stays live).
5. **Remote-source scope creep** (HF SDK, registry APIs) — keep sources optional plug-ins; no new mandatory dependency. `requests`/`httpx` already present.
6. **UI payload growth** — richer ModelRecord could bloat `/api/models/discovery`; add field projections per surface.
7. **Migration of `models.automatic.setup.dismissed`** — one-way rename with back-compat read for one release.
8. **Locked-constraint enforcement** — extend `test_architecture.py` patterns to forbid name-keyed conditionals in discovery/eligibility/recommendation code, and forbid `eligible*`/`Automatic` model vocabulary in payloads.

---

## 18. Tests that should be added

**Discovery / metadata**
- `test_discovery_parses_show_payload.py` — Ollama `/api/show` parsing: params/quant/context/family/license; missing fields → `None`, never fabricated.
- `test_runtime_inventory_abstraction.py` — inventory providers satisfy a common contract; unknown runtime returns empty, not error.
- `test_model_identity_parsing.py` — `qwen3:8b`, `llama3.2:3b-q4_K_M`, HF ids, LM Studio names parse into structured identity with unknown-safe gaps.

**Constraints (architecture guards)**
- Extend `test_architecture.py`: production may not contain model-name substring conditionals (`llava`, `coder`, `-vl`, …) outside labeled seed data; no `eligible*`/`Automatic`/`Custom` model vocabulary in serialized payloads; `KNOWN_MODEL_FACTS`-style data not imported as authority by eligibility/recommendation modules.
- `test_no_model_name_conditionals.py` — AST scan: forbid `if model ==`/`in ("..."` model literals in production logic.

**Recommendation**
- `test_recommendation_unknown_model.py` — a model absent from seed data, with measured capabilities, receives capability-matched advice with explicit provenance (no name-keyed exclusion).
- `test_recommendation_never_writes_config.py` — `recommend()` pure; only `apply_selection` writes; property-style test over seeded hardware profiles.
- `test_use_recommended_only_apply_path.py` — install completion / discovery refresh never mutates `llm.workloads.*` (extend existing `test_resolver_integration_webui.py`).

**UI**
- `test_models_library_tabs.py` — Installed / Recommended / Explore render from new payload; details drawer shows measured fields; compatibility panel shows provenance.
- `test_models_settings_payload_schema.py` — payload type changes are round-tripped by both frontend types and backend serializers (schema-drift guard).

**Acquisition**
- `test_install_routes_to_source.py` — installing an `available` model resolves its source (runtime/registry) and routes to the matching acquirer.
- `test_install_dedup_and_progress.py` — in-flight dedup + progress events across runtime types.

**Cache / offline**
- `test_metadata_cache.py` — TTL, invalidation on install/uninstall/config change, daemon-down fallback to cached inventory (labeled stale).
- `test_offline_inventory.py` — daemon unreachable → config-referenced models surfaced as `missing`, cached installed list served with `stale` marker, runtime validation never uses stale data as authoritative.