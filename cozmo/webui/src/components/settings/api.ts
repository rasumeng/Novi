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
}

export interface CapabilityEvidence {
  capability: string
  supported: boolean | null
  source: string
  confidence: number | null
  note: string
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
  family?: string | null
  variant?: string | null
  quantization?: string | null
  parameterCount?: string | null
  contextLength?: number | null
  format?: string | null
  license?: string | null
  capabilityEvidence?: CapabilityEvidence[]
  stale?: boolean
}

export interface WorkloadRecommendation {
  workload: string
  model: string
  capability: string
  qualification: string
  hardwareConfidence: string
  reasons: string[]
  caveats: string[]
  capabilities: string[]
  visionCapable: boolean
}

export interface RecommendationsPayload {
  workloads: Record<string, WorkloadRecommendation>
  provisional: boolean
}

export interface DiscoveryPayload {
  hardware: { ramGb: number }
  models: DiscoveredModelEntry[]
  missingModels: string[]
  installedNames: string[]
  dismissedRecommended: string[]
  workloads: Record<string, string>
  recommended: RecommendationsPayload
  vision_capable: boolean
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
    dismissedRecommended: [],
    workloads: { general: '', research: '', code: '' },
    recommended: { workloads: {}, provisional: true },
    vision_capable: false,
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
 * Persist the user's workload -> model selection verbatim. The backend never
 * auto-populates selection; recommendations are advisory only.
 */
export async function saveWorkloadSelection(workloads: Record<string, string>): Promise<{ ok: boolean; workloads?: Record<string, { status: string }>; error?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/configuration/models/selection`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workloads }),
    })
    return r.json()
  } catch {
    return { ok: false, error: 'request failed' }
  }
}

/**
 * Explicit "Use Recommended": apply the advisory recommendations as the
 * selection. Advisory-only unless ``apply`` is true — recommendations never
 * auto-apply and never install anything.
 */
export async function applyRecommendedModels(): Promise<{ ok: boolean; workloads?: Record<string, string>; error?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/configuration/models/recommend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apply: true }),
    })
    return r.json()
  } catch {
    return { ok: false, error: 'request failed' }
  }
}

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
 * M3.4 — record an explicit decline of a recommended-model install. The user
 * simply says "not now"; nothing is installed and the model stays installable
 * from the Model library with a fresh explicit consent.
 */
export async function dismissRecommendedModel(name: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/configuration/models/setup/dismiss`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    return r.json()
  } catch {
    return { ok: false, error: 'request failed' }
  }
}