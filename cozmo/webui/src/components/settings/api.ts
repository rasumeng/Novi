import type { SettingsData } from './types'

const API_BASE = import.meta.env.DEV ? 'http://localhost:8765' : ''

export { API_BASE }

// ── Legacy config (kept for non-settings consumers) ─────────────────────

export async function fetchConfig(): Promise<SettingsData> {
  const r = await fetch(`${API_BASE}/api/config`)
  return r.json()
}

export async function saveConfig(patch: Record<string, unknown>) {
  await fetch(`${API_BASE}/api/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export async function fetchOllamaModels(): Promise<string[]> {
  try {
    const r = await fetch(`${API_BASE}/api/ollama/models`)
    if (r.ok) return r.json()
  } catch {}
  return []
}

export async function fetchAvailableModels(): Promise<{ name: string; provider: string }[]> {
  try {
    const r = await fetch(`${API_BASE}/api/models/available`)
    if (r.ok) return r.json()
  } catch {}
  return []
}

// ── Configuration Framework API (Settings V2) ───────────────────────────

export interface SettingSchema {
  id: string
  label: string
  description: string
  category: 'general' | 'models' | 'agent' | 'memory' | 'skills' | 'connectors' | 'permissions' | 'developer'
  owner: string
  type: string
  default: unknown
  options: { value: unknown; label: string; description: string }[]
  restart_required: boolean
  depends: string[]
  visibility: string
}

export interface SchemaResponse {
  settings: SettingSchema[]
  groups: { key: string; label: string; description: string; category: string; owner: string; settings: SettingSchema[] }[]
}

export interface ModelEligibility {
  hardwareFit: string
  hardwareConfidence: string
  eligibleAutomatic: boolean
  eligibleCustom: boolean
}

export interface DiscoveredModelEntry {
  name: string
  status: 'installed' | 'available' | 'missing'
  size: number | null
  capabilities: Record<string, boolean>
  recommended: boolean
  tier: 'supported' | 'experimental'
  qualification?: string
  reasons: string[]
  displayName: string
  approxRamGb: number | null
  caveats?: string[]
  eligibility?: ModelEligibility
}

export interface DiscoveryPayload {
  hardware: { ramGb: number }
  models: DiscoveredModelEntry[]
  missingModels: string[]
  installedNames: string[]
  presets: { id: string; label: string; description: string; lightweight: boolean }[]
  activeExperience: string
  roles: Record<string, string>
}

export async function fetchSchema(): Promise<SchemaResponse> {
  const r = await fetch(`${API_BASE}/api/configuration/schema`)
  return r.json()
}

export async function fetchFrameworkConfig(): Promise<Record<string, unknown>> {
  const r = await fetch(`${API_BASE}/api/configuration`)
  return r.json()
}

export async function fetchDiscovery(): Promise<DiscoveryPayload> {
  try {
    const r = await fetch(`${API_BASE}/api/models/discovery`)
    if (r.ok) return r.json()
  } catch {}
  return {
    hardware: { ramGb: 0 },
    models: [],
    missingModels: [],
    installedNames: [],
    presets: [],
    activeExperience: 'medium',
    roles: {},
  }
}

/** Live-persist a single setting through the framework (no Save needed). */
export async function setSetting(settingId: string, value: unknown): Promise<boolean> {
  try {
    const r = await fetch(`${API_BASE}/api/configuration/${encodeURIComponent(settingId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, by: 'web' }),
    })
    const body = await r.json()
    if (body.error) {
      console.warn(`[config] ${settingId}: ${JSON.stringify(body.error)}`)
      return false
    }
    return true
  } catch (e) {
    console.warn(`[config] ${settingId} failed`, e)
    return false
  }
}

/**
 * Backend experience/preset migration compatibility:
 * the backend `/api/configuration/apply` and the preset machinery remain for
 * one-way migration only. The frontend no longer exposes experience presets,
 * so no client is kept here.
 */

/** Start a model install in the background. Progress arrives over WS. */
export async function installModel(name: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/models/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    return r.json()
  } catch {
    return { ok: false, error: 'request failed' }
  }
}

/**
 * Drive the M3.2 Automatic <-> Custom model state machine on the backend.
 *
 * ``mode``: "automatic" | "custom".
 * ``assign``: capability -> model intent to persist for Custom mode before the
 * backend resolves everything to ``llm.roles.*`` through the Configuration
 * Framework. Capability keys are limited to chat/reasoning/coding/vision.
 */
export async function setModelsState(
  mode: 'automatic' | 'custom',
  assign?: Record<string, string>,
): Promise<{ ok: boolean; mode?: string; error?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/configuration/models/state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, assign }),
    })
    return r.json()
  } catch {
    return { ok: false, error: 'request failed' }
  }
}