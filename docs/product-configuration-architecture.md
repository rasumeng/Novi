# Cozmo Product Configuration Architecture

Blueprint for the Settings redesign (Phase 1+). Defines the product-level
concepts the Settings UI should be built around, instead of the raw config
keys it exposes today. Nothing here is implemented yet — this is the schema
the redesign will target.

## Why this document exists

The current Settings UI is a thin form over one loosely-typed JSON blob
(`SettingsData` = `{ [key: string]: unknown }`). Every section reads/writes
directly into that blob, which is why it reads as a developer config page:
there's no product concept standing between "the backend's config file" and
"what the user sees." Before redesigning the UI, we need real names and
shapes for the things a user actually configures.

## Core concepts

### Performance Profile

A Performance Profile is the user-facing answer to "how should Cozmo run on
my machine, right now." It's the thing shown in onboarding and in Settings →
General, and it's the direct evolution of today's `runtime.lightweight_mode`
flag — not a replacement for it.

```ts
interface PerformanceProfile {
  id: 'lightweight' | 'balanced' | 'high_quality' | 'custom'
  label: string              // "Lightweight", "Balanced (Recommended)", ...
  description: string        // one sentence, plain language
  recommendedFor: string     // "Laptops, low RAM, battery life"
  modelPreset: ModelPresetId // which Supported Profile of models it uses
  // Forward-looking knobs — inert today, real once backend adopts them:
  reasoningDepth: 'low' | 'standard' | 'deep'
  retrieval: 'minimal' | 'standard' | 'thorough'
  memory: 'light' | 'standard' | 'full'
}
```

Today: selecting a profile writes `runtime.lightweight_mode` (for
`lightweight` specifically) plus a full `llm.roles` object (via the model
preset it references) through the existing `PUT /api/config`. `reasoningDepth`
/ `retrieval` / `memory` are captured in the UI's local profile definition
but have **no backend effect yet** — see "what's inert" below. `custom` means
"the user left the wizard and is editing Advanced/Developer settings
directly," and disables profile-driven writes entirely.

This is the piece the user was explicit about: **keep the flag, don't delete
it, treat it as the seed of this system.**

### Supported Model / Model Catalog Entry

A curated, tested entry — the opposite of "whatever string happens to be in
the config file."

```ts
interface ModelCatalogEntry {
  id: string                 // the real model id passed to the provider
  displayName: string        // "Llama 3.1 8B", not "llama3.1:8b-instruct-q4"
  provider: 'ollama' | 'openai' | ...
  tier: 'supported' | 'experimental'
  sizeParams?: string        // "8B" — display only
  approxRamGb?: number       // rough sizing hint for onboarding/profile fit
  speed: 'fast' | 'balanced' | 'slow'
  quality: 'good' | 'better' | 'best'
  capabilities: Array<'chat' | 'coding' | 'vision' | 'reasoning'>
  recommendedRoles: string[] // which of today's 7 backend roles this suits
}
```

`tier: 'supported'` entries are the curated, tested set (ships with the app,
hand-maintained). `tier: 'experimental'` entries are anything else the
backend can see — today that means whatever `GET /api/models/available`
returns via live Ollama/provider discovery — surfaced with a visible
"experimental, unverified" badge instead of hidden.

### Model Preset (the thing a Performance Profile points at)

```ts
interface ModelPreset {
  id: string
  label: string
  roleAssignments: Record<string, string> // today's 7 role names -> model id
}
```

This is the direct, structured replacement for hand-picking a model per role
in the current Models settings. It's what actually gets written into
`llm.roles` via `PUT /api/config` — the *only* part of this whole document
that already has a real, working backend write path today.

### Capability Metadata

Small, reusable descriptors used across Models, onboarding, and (eventually)
Tools/Connectors — the pattern the existing `PRESET_META` /
`CAPABILITY_DEFS` constants already do informally for MCP capabilities. This
document promotes that pattern to a first-class, shared concept instead of
being redefined ad hoc per settings section.

## What belongs in Advanced / Developer Mode

Anything that exposes backend implementation vocabulary directly, rather
than a product concept:

- Per-role model assignment (today's classifier/router/orchestrator/etc.)
- Temperature, max steps, agent system prompt
- Raw MCP server command/args/env, raw config/diagnostics JSON
- Per-tool Allow/Ask/Deny matrix
- Memory tunables (`max_turns_before_summary`, `max_short_term_pairs`, distance
  thresholds)
- Anything in `RuntimeConfig` with no product-level surface today
  (`max_history`, `max_tool_output_chars`, `memory_distance_threshold`, etc.)
- Provider credentials entered as raw env-var names

This isn't a "hide it forever" list — it's "the audience for this is someone
who wants the current dev-config experience on purpose." Advanced mode should
still exist, just as an explicit, opted-into destination rather than a
side-effect of an unrelated toggle.

## Which concepts should eventually move server-side

Ranked by how much value moving them would unlock, since none of this is
urgent:

1. **Performance Profile as a real backend concept.** Right now
   `lightweight_mode` is read by nothing. For the profile system to mean what
   its name promises (reasoning depth, retrieval, memory all shifting
   together), the backend needs to read an active-profile field and branch on
   it. This is the highest-value backend addition, and the one the user
   flagged as intentional future work — not something to fake indefinitely.
2. **Model Catalog validation.** `/api/config` accepts any role/model string
   today with zero validation. A backend-side catalog (even just "does this
   model id actually exist for this provider") would let bad configuration
   fail at save time instead of mid-conversation.
3. **Capability metadata as backend-reported fact** (e.g., "this model
   supports vision") instead of frontend-curated guesses — removes the risk
   of the catalog drifting from what a model can actually do.

## What stays frontend-only, indefinitely

- The Performance Profile *picker UI*, onboarding flow, and all copy/labels.
- The Supported/Experimental split as *presentation* — even after the backend
  adopts profiles, the curated-vs-discovered distinction is a display concern.
- Settings information architecture and navigation.
- Non-functional UI state: search filters, section routing, form drafts.

## Adapter layer (how this maps onto today's config, unchanged)

No backend change ships with Phase 1. The Settings redesign is a translation
layer sitting in front of the existing `PUT /api/config` merge:

```
PerformanceProfile ──▶ writes runtime.lightweight_mode (lightweight only, as today)
                   ──▶ writes llm.roles via its ModelPreset
ModelCatalogEntry  ──▶ id passed straight through as llm.roles[role]
Advanced mode      ──▶ today's raw section UIs, relocated, not rebuilt
```

Because this is a translation layer and not a rewrite of the backend, the
same shapes defined here are what a future backend-owned profile system
would adopt — the frontend isn't inventing a throwaway shape, it's staging
the real one.
