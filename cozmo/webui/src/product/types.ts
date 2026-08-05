// Product-level configuration concepts. See docs/product-configuration-architecture.md
// for the rationale — these types are the shapes defined there, made real.
//
// Nothing here is backend-enforced: the backend accepts arbitrary config today
// (see that doc's "risk analysis"), so this layer is where validity actually
// lives until/unless the backend adopts these shapes itself.

export type PerformanceProfileId = 'lightweight' | 'balanced' | 'high_quality' | 'custom'

export type ModelCapability = 'chat' | 'coding' | 'vision' | 'reasoning' | 'embeddings' | 'tools'

export type ModelTier = 'supported' | 'experimental'

export interface ModelCatalogEntry {
  id: string
  displayName: string
  provider: 'ollama' | 'openai'
  tier: ModelTier
  sizeParams?: string
  approxRamGb?: number
  speed: 'fast' | 'balanced' | 'slow'
  quality: 'good' | 'better' | 'best'
  capabilities: ModelCapability[]
  /** Which of the backend's fixed routing roles (see settings/constants.ts BUILTIN_ROLES) this model suits. */
  recommendedRoles: string[]
}

export interface ModelPreset {
  id: string
  label: string
  /** BUILTIN_ROLES role name -> catalog model id. */
  roleAssignments: Record<string, string>
}

export interface PerformanceProfileLearnMore {
  /** What actually changes when this profile is selected — plain language, no role names. */
  whatChanges: string
  /** Why this profile exists as a distinct choice. */
  whyItExists: string
  /** Who should pick this one. */
  whoShouldUse: string
  /** Hardware guidance, expanded from the card's one-line `recommendedHardware`. */
  hardware: string
}

export interface PerformanceProfile {
  id: PerformanceProfileId
  label: string
  /** One line: what this profile is optimized for — "conversation, coding, memory, and general use." */
  intendedUse: string
  /** One or two sentences of plain-language description shown on the card. */
  description: string
  /** Short hardware guidance shown directly on the card, e.g. "Most laptops and desktops". */
  recommendedHardware: string
  /** null only for 'custom' — custom means "don't manage model assignment at all." */
  modelPresetId: string | null
  learnMore: PerformanceProfileLearnMore
  // Forward-looking knobs: captured now, inert until the backend reads them
  // (see docs/product-configuration-architecture.md's "what's inert" note).
  reasoningDepth: 'low' | 'standard' | 'deep'
  retrieval: 'minimal' | 'standard' | 'thorough'
  memory: 'light' | 'standard' | 'full'
}
