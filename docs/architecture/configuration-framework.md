# Cozmo Configuration Framework (Settings V2)

Milestone 4.5 architecture document. Defines the replacement of Cozmo's
configuration architecture. This is **not** a UI redesign or a refactor — it
is a replacement of how configuration is owned, routed, validated, and applied
at runtime.

Status: approved-for-implementation (Milestone 4.5). Durable architecture, do
not archive.

---

## 1. Current Settings architecture

Today "settings" is spread across four disconnected layers with no single
owner.

### 1.1 Backend: `cozmo/config.py`

A module-global TOML loader. `load()`:

1. Reads `~/.cozmo/config.toml` (or returns a hardcoded `DEFAULT_CONFIG`).
2. Auto-migrates legacy `[models]` → `[llm.roles]`.
3. Synthesizes a **virtual legacy `models` section** for old code paths
   (`_apply_backward_compat`).
4. Deep-merges defaults (`_merge_defaults`).
5. Resolves paths, applies env-var overrides, strips `None`.

Problems:
- `DEFAULT_CONFIG` hardcodes model names (`qwen3:8b`, `nomic-embed-text`) and
  routing tables (`intent_roles`, `capability_roles`, `capability_preferences`).
- The `models`-mirror is a second source of truth generated next to `llm.roles`.
- No schema, no validation, no per-key owner, no change events, no runtime
  propagation. It returns one opaque dict.

### 1.2 Consumers read the raw dict everywhere

Every subsystem reaches into the same flat dict with `.get(key, fallback)`:

- `cozmo/runtime/runtime.py` reads `cfg["llm"]["default_model"] or "qwen3:8b"`,
  `cfg["runtime"]["routing"]`, `tool_gate`, `planning`, `memory`, etc.
- `cozmo/runtime/model_router.py` defaulting to `default_model="qwen3:8b"`.
- `cozmo/services/context.py` (`ollama_url`, memory tuning, brain wiring).
- `cozmo/memory/manager.py`, `knowledge_index.py`, `brain/storage/*` default to
  `nomic-embed-text`.
- `cozmo/providers/base.py` parsing.

These are hardcoded fallbacks scattered across the codebase — exactly what the
milestone forbids.

### 2.3 Frontend: `cozmo/webui/src/components/settings/`

React layer. `SettingsModal` hosts four pages (Settings / Models / Advanced /
Developer) that render **schema-driven** fields straight from the framework and
persist each change immediately via `POST /api/configuration/{setting}` — no
Save button, no local draft blob. `useFrameworkSettings` loads schema +
discovery + live config and subscribes to `config_updated` / `install_progress`
WebSocket frames:

- `Settings` — General: experience preset cards, hardware readout, missing-model install.
- `Models` — live discovery grid (status, recommendations, install buttons).
- `Advanced` — schema-driven `SettingField`. Advanced category + Memory /
  Tools / Connectors (MCP) / Agent / Skills relocated here.
- `Developer` — schema-driven Developer category: expert routing, providers,
  embeddings.

The legacy `product/*` adapter layer (presets / catalog / configLayer / profiles
/ modelTiers / capabilities) and its UI components (`ProfilePicker`,
`ModelRolePicker`, `ModelCatalogCard`, `ExpertModelConfig`, `useProductConfig`)
were **deleted** — their role (presets, discovery, recommendations) moved
server-side.

### 2.5 Bridge

`cozmo/webui_server.py`:
- `GET /api/config` → returns the whole dict.
- `PUT /api/config` → `deep_merge` into the shared `cfg`, write TOML, then
  refresh only MCP. **No validation, no events, no runtime reload for most
  keys.**
- `GET /api/ollama/models` → live tags.
- `GET /api/models/available` → `model_service.list_available()`.
- No install endpoint. No download progress. No recommendations.

---

## 2. Sources of truth (audit)

| Concept | Backend owner | Frontend owner | Conflict |
|---|---|---|---|
| `llm.roles` (per-role model) | framework `llm.roles.{role}.model` | Developer page `SettingField` | resolved |
| `models` (legacy mirror) | framework (migrated once to `llm.roles`) | — | removed |
| Role defaults / routing | framework presets + `resolve_preset` | Settings page experience cards | resolved |
| Supported model catalog | `catalog.py` curated facts | Models page (discovered state) | resolved |
| Presets | `presets.py` backend | Settings page experience cards | resolved |
| Recommendations | `ModelRecommendationEngine` | Models page `reasons` | resolved |
| Embedding default model | framework `embedding.model` (no fallback) | Developer page | resolved |
| Per-tool permissions | framework `permissions.*` | Tools (Advanced) | single, OK |
| MCP servers | framework `mcp.*` | Connectors (Advanced) | single, OK |

**Cumulative harm:** the same concept is edited in 2–4 places, has 1–2 default
fallbacks, and no single owner.

---

## 3. Product debt & legacy components

- **`models` legacy mirror** (`_apply_backward_compat`) — synthesize-and-keep,
  a fake parallel source. Remove; migrate read paths to `llm.roles`.
- **`_OLD_MODEL_ROLE_MAP` / `_migrate_from_old`** — historical migration from
  pre-Phase-C config. Retain only as an idempotent one-way migration in the new
  framework's migration layer; drop from runtime load path.
- **`ModelRouter(default="qwen3:8b")`** and `_CAPABILITY_PREFERENCE` default —
  delete. Router must be configured by the framework or raise.
- **Hardcoded uid-embed defaults** in `code_indexer`, `knowledge_index`,
  `memory/manager`, `brain/storage/*`, `services/embedding_providers` —
  remove; read from framework, error-don't-fallback.
- **`runtime().cfg` dict** — replace with a framework-backed typed config
  object that the executor reads keys from.
- **Frontend `product/*` hardcodes** — DELETED; backend `model discovery` and
  `recommendations` now source the UI. Experience presets moved server-side.
- **`expert` UI routing / `ExpertModelConfig`** — DELETED; developer page
  renders per-role schema settings.
- **Dead / unused keys** — serialize-then-diff to prune (below).
- **`SettingsModal` Save button** — the milestone forbids it; delete.

---

## 4. Runtime configuration flow (proposed)

```
                     ┌──────────────────────────────────────────────┐
 user edits UI  →   │            Configuration Framework           │
                     │  Registry  Schema  Validator  State  Events  │
                     │  Persistence  Migration  Discovery  Install   │
                     └──────────────┬───────────────┬───────────────┘
                      validates & persists          emits change events
                                     ▼               ▼
                           config file (owned)      EventBus: config.changed.<path>
                                                     └─▶ subscribers react
                                                     └─▶ MCPManager.refresh_servers
                                                     └─▶ runtime.ModelRouter.reload
                                                     └─▶ memory.Embedding reload
                                                     └─▶ ui broadcasts S2C

  API surface: GET /api/configuration/schema
               GET /api/configuration (typed, resolved)
               PATCH /api/configuration/<path>   (validated, persisted, emitted)
               WS /configuration  (compound change feed)
```

Two personalities:

1. **Owners/UI** talk to the framework (typed, validated, event-driven).
2. **Readers** (runtime internals) subscribe to the config event bus and read
   resolved values from framework state.

No subsystem ever re-reads `config.toml` directly.

---

## 5. Proposed Configuration Framework

New package `cozmo/configuration/`.

| Module | Responsibility |
|---|---|
| `schema.py` | `Setting`, `SettingGroup`, validation, types, categories |
| `registry.py` | `register()`, `get()`, `groups()`, ownership model |
| `store.py` | persistence wrapper (TOML), merge, write, backup |
| `state.py` | resolved runtime values, `get(path)`, flattened view |
| `events.py` | typed `config.updated.<group>` event dispatch (thin, own bus) |
| `manager.py` | facade: `get(s)`, `set(path,val)`, `subscribe`, `snapshot` |
| `migration.py` | legacy `[models]`→`[llm.roles]`, org, hard-record removal |
| `discovery.py` | Ollama/model enumeration → Installed/Missing/Ext/Rec |
| `install.py` | Ollama pull with progress |
| `presets.py` | Experience presets (Light/Medium/Heavy/Custom) → routing blocks |
| `catalog.py` | known-model facts (size, capabilities) + compatibility signals |

### Schema shape

```python
@dataclass
class Setting:
    id: str                  # e.g. "llm.roles.chat.model"
    label: str               # "Conversation model"
    description: str
    category: Category       # GENERAL | MODELS | ADVANCED | DEVELOPER
    owner: str               # runtime | memory | brain | mcp | skills | providers
    type: Type               # string | int | float | bool | enum
    default: Any
    validation: list[Validator]
    options: list[Option]    # for enum / model pickers
    restart_required: bool
    depends: list[str]
    visibility: str          # "user" | "advanced" | "developer"
    sensor: str              # how this config: "direct" only
```

### Registry example (runtime)

```python
require("llm")
require("runtime", owner="runtime")
cfg_register(Setting(id="llm.default_model", label="Default model",
                     category=MODELS, owner="runtime", type=MODEL,
                     default="", restart_required=True))
cfr_register(Setting(id="llm.roles.chat.model", ... owner="runtime"))
```

Registration is done by the owning subsystem, not by a Settings page.

## 6. Event model

Every change emits an event on a tiny internal bus:

```
event: cozmo.config.updated
  data: { path, value, previous, by }
```

Subsystems subscribe with a path prefix or wildcard:

- `runtime` subscribes `llm.*` → rebuild `ModelRouter`.
- `memory` subscribes `embedding.*` → reload embedding provider.
- `mcp` subscribes `mcp.servers` → reconnect.
- UI bridges `config.updated` to the WebSocket so pages stay live.

`runtime.never_poll`: no polling. All reactive.

## 7. Registration model (mirrors ownership)

```
ConfigurationFramework.register(Setting, owner, apply)
   apply: callable(config_state) → reload that subsystem
```

Each subsystem self-registers and supplies an `apply` callback. The Settings UI
renders the union of registered settings, grouped by category — it never
hardcodes pages.

---

## 6. Remove hardcoded models

Repository-wide scan for literals:
`qwen3`, `llama3`, `phi3`, `llava`, `nomic-embed`, `cross-encoder`,
`gemma`, `gpt-` — all to be removed.

Each specialty:

### Model `default` in `config.py`
Replace `llm.default_model = "qwen3:8b"` with `type: null` and treat empty as "not
configured". The framework offers discovery + install.

### ModelRouter default
Gone. Router required the active routing graph from the config; if empty, it
emits `model.unselected` (the UI shows install/select), never silently
substitutes.

### Remove silent fallbacks
Every `.get(key, hardcoded)` replaced with a resolver that:
1. reads resolved value from state,
2. if unset/unsupported → `SettingNotConfigured` signal,
3. surfaces to UI.

---

## 7. Dynamic model discovery

`discovery.py` queries the live Ollama `/api/tags` (and provider /list_models).
Returns unified `DiscoveredModel`:

```python
class DiscoveredModel:
    name:str; provider:str; status:
        INSTALLED | AVAILABLE(remote) | MISSING
    size: bytes|None
    capability_flags: ...
```

`catalog.py` cross-references with known facts → `recommended`, `tested`,
`hardware_fit` (VRAM/RAM vs. approx). Recommendations are always annotated:

```
Recommended (Tested with Cozmo)
Recommended (Best for your hardware: 16 GB RAM)
Recommended (Works with Memory)
Recommended (Supports Tool Calling)
```

## Presets → routing (no hardcoded model names)

`presets.py` maps an Experience to a **routing block** (roles-to-model *names*
remain empty until the user selects an installed model). Presets only reorder
_which installed model_ the role assignments resolve to; they never embed
literals. If a preset requires a missing model, the framework surfaces
"Install" and streams pull progress via the install submodule.

### Custom experience

Exposes the whole routing table in one page — no cross-page navigation. Every
settable routing key is editable, validated, and persisted immediately.

## Auto-install

- `install.py` wraps `POST /api/ollama/tags` pull, streams progress over the
  WebSocket (`install.progress`).
- UI shows available size, progress bar, and completes by re-discovering.

## 5. Remove Save

- Framework persists on `set()`. UI calls `PATCH /api/configuration/{key}`
  (or a batched `PATCH /api/configuration`) — each applied+persisted instantly.
- The `Save` button and dirty-latch in `SettingsModal` are removed.

## 6. UI redesign (only after framework)

Four pages, single responsibility:

| Page | Purpose |
|---|---|
| **General** | How Cozmo behaves (isExperience auto) |
| **Models** | What Cozmo runs (discovery, presets, install) |
| **Advanced** | How Cozmo executes (memory, tools, permissions, providers) |
| **Developer** | Diagnostics + experimental controls |

`SettingsUI reads the registered schema from `GET /api/schema` rather than
hardcoding sections/roles. The `SettingsModal` is a thin, live-consuming
presentation layer.

---

## 8. Migration strategy

1. Land `cozmo/configuration` new package, unused until ready.
2. Add `Configuration` implementation; route `config.load()` through new
   resolver, keeps TT backward `models` projection off.
3. Migrate consumers one subsystem at a time to read from framework; then().
4. Server endpo:ds to `.api/configuration`.
5. Delete legacy `config.py` dict path once all consumers moved.

Migration of real user files: one-directional, idempotent (old `models` →
`llm.roles`), committed as a `migrate.py` step with DB style backup.

## 9. Risks

- **Surface of `.get()` fallbacks** — audit must be exhaustive; missed ones
  re-introduce silent defaults. Mitigation: a dedicated test greps for
  `default=` in model contexts and asserts none addresses `llm`/`embedding`.
- **UI/backend drift returns if schema not the single source** — enforce the
  front end consumes `GET /api/schema`; a test asserts a superset relation.
- **Runtime-reload complexity** — some settings (agents/default model) need a
  rebuild. `restart_required` on those settings, surfaced in UI + response.
- **Loss of per-model sem till discovery** — recommendations degrade to "+".
- **e.g.** — big concurrent change; done in phases.

## 10. Implementation phases

- **P0 (this milestone)** — package skeleton + schema + registry + store +
  events + manager; move/remove hardcoded model defaults; dynamic discovery;
  recommendations; presets; install; UI wiring; delete Save.
- **P1-cobra** — migrate consumers to framework reads; route `config.py` load
  through the manager; remove `models` mirror.
- **P2** — single-source schema + UI schema-driven render; destroy legacy dict
  surface.

---

## Exit criteria / DoD (each item from the milestone statement mapped to a test)

- Every setting one owner. → registry asserts unique `owner`.
- Every visible setting has runtime effect. → every schema `id` maps to ≥
  one event consumer.
- Event-driven. → change → bus emit → consumer apply, no polling.
- No Save. → UI has no save button.
- No hardcoded models. → prod grep clean.
- No silent fallbacks. → resolver raises/empty, not default.
- Runtime never reads config file. → runtime pulls from manager/state only.
- Subsystems integrate by registration. → each owns an `apply`.
- Settings UI is a presentation layer only over schema.