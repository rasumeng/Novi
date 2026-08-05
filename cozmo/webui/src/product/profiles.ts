import type { PerformanceProfile } from './types'

// The direct evolution of runtime.lightweight_mode — not a replacement.
// Selecting 'lightweight' still sets that flag; it now also applies a real
// model preset instead of being a cosmetic toggle. reasoningDepth/retrieval/
// memory are captured per profile but inert until the backend reads them —
// see docs/product-configuration-architecture.md.
//
// These read as an experience to choose, not a setting to flip: each one
// answers "what changes," "why does this exist," and "who is it for" via
// `learnMore`, meant for a small explanatory panel, not a full doc page.
export const PERFORMANCE_PROFILES: PerformanceProfile[] = [
  {
    id: 'lightweight',
    label: 'Lightweight',
    intendedUse: 'Fast responses on lower-end hardware',
    description: 'Designed for lower-end hardware and faster responses. One small model handles everything.',
    recommendedHardware: 'Laptops, limited RAM, battery life',
    modelPresetId: 'lightweight',
    learnMore: {
      whatChanges: 'Cozmo uses a single small model for every part of the conversation — replies, tool use, and vision all share one lightweight model instead of specialized ones.',
      whyItExists: "Bigger, specialized models need more memory and are slower to respond. This profile trades some quality on hard tasks for speed and a much smaller footprint.",
      whoShouldUse: "You're on a laptop, have limited RAM, or want the snappiest possible responses over the best possible ones.",
      hardware: 'Comfortable on most laptops — roughly 4-8GB of RAM free for Cozmo.',
    },
    reasoningDepth: 'low',
    retrieval: 'minimal',
    memory: 'light',
  },
  {
    id: 'balanced',
    label: 'Balanced (Recommended)',
    intendedUse: 'Conversation, coding, memory, and general use',
    description: 'Best for most users. Optimized for conversation, coding, memory, and general use.',
    recommendedHardware: 'Most laptops and desktops',
    modelPresetId: 'balanced',
    learnMore: {
      whatChanges: 'Cozmo mixes a few small and mid-sized models by task — a dedicated coding model for code, a stronger model for conversation and planning, and a vision model for images.',
      whyItExists: 'Most tasks work better with a model suited to them, without needing the largest possible model everywhere. This is the mix Cozmo is tuned and tested with.',
      whoShouldUse: "You're not sure which profile to pick. This is the recommended default for most people.",
      hardware: '16GB+ RAM recommended for smooth performance.',
    },
    reasoningDepth: 'standard',
    retrieval: 'standard',
    memory: 'standard',
  },
  {
    id: 'high_quality',
    label: 'High Quality',
    intendedUse: 'Maximum reasoning quality on powerful systems',
    description: 'Maximizes reasoning quality on powerful systems, at the cost of speed and memory use.',
    recommendedHardware: 'Workstations with 32GB+ RAM or a strong GPU',
    modelPresetId: 'high_quality',
    learnMore: {
      whatChanges: 'Cozmo switches to the largest models it supports for conversation, coding, and planning — better answers on hard, multi-step tasks, at the cost of speed.',
      whyItExists: "Some tasks — deep research, complex code, multi-step plans — genuinely benefit from a larger model. This profile is for when quality matters more than response time.",
      whoShouldUse: "You have a capable machine (lots of RAM or a strong GPU) and want the best answers Cozmo can give, even if replies take longer.",
      hardware: 'Needs 32GB+ RAM, or a GPU with enough VRAM to keep large models fast.',
    },
    reasoningDepth: 'deep',
    retrieval: 'thorough',
    memory: 'full',
  },
  {
    id: 'custom',
    label: 'Custom',
    intendedUse: 'Full manual control over every model',
    description: "Pick every model yourself. Cozmo won't manage this for you.",
    recommendedHardware: 'Any — depends entirely on what you choose',
    modelPresetId: null,
    learnMore: {
      whatChanges: "Cozmo stops managing model choice for you. Advanced settings let you assign models yourself, down to the individual model role if you want that level of control.",
      whyItExists: 'Some people already know exactly which models they want and don\'t need Cozmo to pick for them.',
      whoShouldUse: "You already have specific models in mind, or want to experiment with combinations Cozmo doesn't officially recommend.",
      hardware: 'Depends entirely on the models you choose in Advanced settings.',
    },
    reasoningDepth: 'standard',
    retrieval: 'standard',
    memory: 'standard',
  },
]

export function getProfile(id: string): PerformanceProfile | null {
  return PERFORMANCE_PROFILES.find((p) => p.id === id) ?? null
}
