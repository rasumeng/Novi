// The adapter layer between product concepts (Performance Profile, Model
// Preset, Supported/Experimental Model) and the existing, unvalidated
// PUT /api/config merge. See docs/product-configuration-architecture.md
// "Adapter layer" — nothing here changes backend behavior; it only decides
// what shape of config patch a product-level choice should produce, and can
// infer the reverse (what profile does this config already look like).

import type { LlmConfig, SettingsData } from '@/components/settings/types'
import { BUILTIN_ROLES } from '@/components/settings/constants'
import { SUPPORTED_MODELS } from './catalog'
import { MODEL_PRESETS, getPreset } from './presets'
import { PERFORMANCE_PROFILES, getProfile } from './profiles'
import type { ModelCatalogEntry, ModelPreset, PerformanceProfileId } from './types'

export { getPreset, getProfile }

/** What config patch applying a profile should produce, merged onto the current config. */
export function profileToConfigPatch(
  profileId: PerformanceProfileId,
  config: SettingsData
): Pick<SettingsData, 'runtime' | 'llm'> | null {
  const profile = getProfile(profileId)
  if (!profile) return null

  const preset = getPreset(profile.modelPresetId)
  if (profileId === 'custom' || !preset) {
    // Custom means "stop managing this for the user" — leave role
    // assignments exactly as they are, just record the flag as off.
    return { runtime: { ...config.runtime, lightweight_mode: false } }
  }

  const roles: LlmConfig['roles'] = {}
  for (const role of BUILTIN_ROLES) {
    roles[role] = { model: preset.roleAssignments[role] }
  }

  return {
    runtime: { ...config.runtime, lightweight_mode: profileId === 'lightweight' },
    llm: { ...config.llm, roles },
  }
}

/**
 * Assign a single backend role to a model — the one place this translation
 * happens, shared by the product-concept pickers (Conversation/Coding/Vision
 * Model) and Expert Configuration's raw per-role editor, so both write the
 * exact same shape instead of each re-deriving it.
 */
export function roleModelPatch(config: SettingsData, role: string, modelId: string): Pick<SettingsData, 'llm' | 'models'> {
  const roles: LlmConfig['roles'] = { ...(config.llm?.roles ?? {}) }
  if (modelId) {
    roles[role] = { model: modelId }
  } else {
    delete roles[role]
  }
  return {
    llm: { ...config.llm, roles },
    // `models` is a legacy mirror some backend paths still read — kept in
    // sync here rather than duplicated at each call site.
    models: { ...config.models, [role]: modelId },
  }
}

/** Assign the embedding model — a config path outside `llm.roles` entirely (see product-configuration-architecture.md). */
export function embeddingModelPatch(config: SettingsData, modelId: string): Pick<SettingsData, 'embedding'> {
  return { embedding: { ...config.embedding, model: modelId } }
}

/** Does `config.llm.roles` exactly match this preset's role assignments? */
function rolesMatchPreset(config: SettingsData, preset: ModelPreset): boolean {
  const roles = config.llm?.roles ?? {}
  return BUILTIN_ROLES.every((role) => {
    const spec = roles[role]
    const model = typeof spec === 'string' ? spec : spec?.model ?? ''
    return model === preset.roleAssignments[role]
  })
}

/**
 * Infer which Performance Profile the live config corresponds to. The
 * backend has no such concept (see the design doc's risk analysis), so this
 * is a pure function of `llm.roles` + `runtime.lightweight_mode` — if
 * neither matches a known preset, the honest answer is 'custom', not a guess.
 */
export function detectActiveProfile(config: SettingsData | null): PerformanceProfileId {
  if (!config) return 'balanced'
  const lightweight = !!config.runtime?.lightweight_mode

  for (const profile of PERFORMANCE_PROFILES) {
    const preset = getPreset(profile.modelPresetId)
    if (!preset) continue
    if (lightweight === (profile.id === 'lightweight') && rolesMatchPreset(config, preset)) {
      return profile.id
    }
  }
  return 'custom'
}

/**
 * Merge the curated catalog with whatever the backend can actually discover
 * (live Ollama tags, other providers). Anything not in SUPPORTED_MODELS is
 * still usable — it's tagged 'experimental' instead of hidden or presented
 * as equally trustworthy.
 */
export function mergeModelCatalog(discovered: { name: string; provider: string }[]): ModelCatalogEntry[] {
  const knownIds = new Set(SUPPORTED_MODELS.map((m) => m.id))
  const merged = [...SUPPORTED_MODELS]
  const seenExperimental = new Set<string>()
  for (const d of discovered) {
    if (knownIds.has(d.name) || seenExperimental.has(d.name)) continue
    seenExperimental.add(d.name)
    merged.push({
      id: d.name,
      displayName: d.name,
      provider: d.provider === 'openai' ? 'openai' : 'ollama',
      tier: 'experimental',
      speed: 'balanced',
      quality: 'good',
      capabilities: ['chat'],
      recommendedRoles: [],
    })
  }
  return merged
}

export { MODEL_PRESETS, PERFORMANCE_PROFILES }

export interface PresetModelSummary {
  modelId: string
  displayName: string
  /** Plain-language purposes this model is used for, e.g. "Conversation", "Coding". */
  usedFor: string[]
}

// Backend routing roles that are pure internal plumbing (see settings/constants.ts
// BUILTIN_ROLES) rather than something a user forms an intent around. Excluded
// from summaries everywhere outside Advanced/Expert mode — see the design doc's
// "Advanced should expose product concepts first" note.
const ROLE_DISPLAY_LABEL: Partial<Record<string, string>> = {
  chat: 'Conversation',
  coder: 'Coding',
  vision: 'Vision',
  orchestrator: 'Planning',
  planner: 'Research & planning',
}

/**
 * Summarize a preset's role assignments as "which models does this actually
 * use, and what are they for" — in the vocabulary a user thinks in, not
 * backend role names. Used by the Profile Picker's collapsed "Models
 * included" section.
 */
export function summarizePreset(preset: ModelPreset, catalog: ModelCatalogEntry[]): PresetModelSummary[] {
  const usedForByModel = new Map<string, string[]>()
  for (const [role, modelId] of Object.entries(preset.roleAssignments)) {
    const label = ROLE_DISPLAY_LABEL[role]
    if (!label) continue
    const usedFor = usedForByModel.get(modelId) ?? []
    usedFor.push(label)
    usedForByModel.set(modelId, usedFor)
  }
  return Array.from(usedForByModel.entries()).map(([modelId, usedFor]) => ({
    modelId,
    displayName: catalog.find((m) => m.id === modelId)?.displayName ?? modelId,
    usedFor,
  }))
}
