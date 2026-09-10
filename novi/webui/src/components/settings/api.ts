const API_BASE = import.meta.env.DEV ? 'http://localhost:8765' : ''

export { API_BASE }

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

export interface PrimaryRecommendation {
  model: string
  qualification: string
  hardwareConfidence: string
  reasons: string[]
  caveats: string[]
  capabilities: string[]
  visionCapable: boolean
  explanation?: RecommendationExplanation | null
}

export interface ExplanationHardwareFit {
  fit: string
  confidence: string
  strength: string
  basis: string[]
}

export interface ExplanationAlternative {
  model: string
  fit: string
  strength: string
  capability: string
  qualification: string
  reasons: string[]
}

export interface RecommendationExplanation {
  provenance: { source: string; confidence: number | null } | null
  hardwareFit: ExplanationHardwareFit | null
  alternatives: ExplanationAlternative[]
  provisional: boolean
}

export interface RecommendationsPayload {
  primary?: PrimaryRecommendation | null
  provisional: boolean
}

export interface DiscoveryHardware {
  ramGb: number
  gpu: {
    name: string
    vramTotalGb: number | null
    vendor: string
  }
  confidence: string
}

export interface ModelCaps {
  vision: boolean
  tools: boolean
  reasoning: boolean
  thinking: boolean
  audio: boolean
  coding: boolean
}

export interface DiscoveryPayload {
  hardware: DiscoveryHardware
  models: DiscoveredModelEntry[]
  missingModels: string[]
  installedNames: string[]
  dismissedRecommended: string[]
  model?: string
  primary?: string
  recommended: RecommendationsPayload
  vision_capable: boolean
  capabilities?: ModelCaps
  capabilityStates?: Record<string, Record<string, string>>
  modelCapabilityStates?: Record<string, Record<string, string>>
  // Task 2.1 — honest Ollama discovery status (additive)
  status?: 'ok' | 'degraded' | 'error'
  ollamaReachable?: boolean
  ollamaUrl?: string
  ollamaError?: string
  modelsStale?: boolean
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
    if (r.ok) {
      const data = await r.json()
      return data as DiscoveryPayload
    }
  } catch {}
  return {
    hardware: { ramGb: 0, gpu: { name: '', vramTotalGb: null, vendor: '' }, confidence: 'unknown' },
    models: [],
    missingModels: [],
    installedNames: [],
    dismissedRecommended: [],
    model: '',
    primary: '',
    recommended: { primary: null, provisional: true },
    vision_capable: false,
    status: 'error',
    ollamaReachable: false,
    ollamaUrl: 'http://localhost:11434',
    ollamaError: 'Failed to reach Novi backend',
    modelsStale: false,
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
 * Persist the user's primary model verbatim. The backend never
 * auto-populates selection; recommendations are advisory only.
 */
export async function savePrimaryModel(model: string): Promise<{ ok: boolean; model?: string; error?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/configuration/models/selection`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    })
    return r.json()
  } catch {
    return { ok: false, error: 'request failed' }
  }
}



/**
 * Explicit "Use Recommended": apply the advisory primary recommendation.
 * Never installs anything.
 */
export async function applyRecommendedModels(): Promise<{ ok: boolean; model?: string; error?: string }> {
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

export function primaryModelFromDiscovery(d: DiscoveryPayload | null): string {
  if (!d) return ''
  return d.primary ?? d.model ?? ''
}
export function primaryRecommendation(d: DiscoveryPayload | null): PrimaryRecommendation | null {
  const rec = d?.recommended
  if (!rec) return null
  if (rec.primary?.model) return rec.primary
  return null
}

/**
 * Canonical fallback for the Memory embedding model. Mirrors the backend
 * ``DEFAULT_EMBEDDING_MODEL`` (novi/configuration/install.py); the live
 * value arrives via framework ``embedding.model`` and the schema default.
 */
export const DEFAULT_EMBEDDING_MODEL = 'nomic-embed-text:v1.5'

/** Resolve the embedding model name: live value wins, then schema default. */
export function embeddingModelFromSchema(schema: SchemaResponse | null, live?: string): string {
  if (live && live.trim()) return live.trim()
  const setting = schema?.settings.find((s) => s.id === 'embedding.model')
  const dflt = typeof setting?.default === 'string' ? setting.default.trim() : ''
  return dflt || DEFAULT_EMBEDDING_MODEL
}

/**
 * True for embedding-only entries (capability ``embedding``/``embeddings``
 * without any user-facing chat capability). Such models power Novi Memory
 * and must never appear as chat-model choices.
 */
export function isEmbeddingOnly(entry: DiscoveredModelEntry): boolean {
  const caps = entry.capabilities ?? {}
  if (['chat', 'reasoning', 'coding', 'vision'].some((c) => caps[c])) return false
  return !!(caps['embedding'] || caps['embeddings'])
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
 * Remove an installed model from disk. This never touches the primary
 * selection — a selected-but-deleted model stays selected and simply
 * becomes missing.
 */
export async function deleteModel(name: string): Promise<{ ok: boolean; error?: string; name?: string }> {
  try {
    const r = await fetch(`${API_BASE}/api/models/delete`, {
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

export interface SystemHealth {
  ready: boolean
  ollama: { ready: boolean; url: string; error?: string | null }
  primaryModel: { name: string; ready: boolean }
  embedding: { model: string; ready: boolean; dimension: number; error?: string | null }
}

export async function fetchSystemHealth(): Promise<SystemHealth | null> {
  try {
    const response = await fetch(`${API_BASE}/api/health`)
    return response.ok ? await response.json() as SystemHealth : null
  } catch {
    return null
  }
}
