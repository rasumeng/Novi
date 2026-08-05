import { MessageSquare, Code2, Eye, Lightbulb, Fingerprint, Wrench } from 'lucide-react'
import type { ModelCapability } from './types'

// Shared, reusable descriptors — the same pattern settings/constants.ts already
// uses informally for MCP capabilities (CAPABILITY_DEFS), promoted here to a
// first-class concept per docs/product-configuration-architecture.md.
// Rendered through the shared <CapabilityBadge> component (components/common),
// which takes icon+label as plain props — this map is what supplies them for
// the "model capability" domain specifically; MCP/tool capabilities keep their
// own metadata map (settings/constants.ts CAPABILITY_DEFS) since they describe
// a different kind of thing, but render through the same badge component.
export const CAPABILITY_METADATA: Record<ModelCapability, { label: string; description: string; icon: React.ElementType }> = {
  chat: {
    label: 'Chat',
    description: 'General back-and-forth conversation and everyday questions.',
    icon: MessageSquare,
  },
  coding: {
    label: 'Coding',
    description: 'Writing, reading, and editing code.',
    icon: Code2,
  },
  vision: {
    label: 'Vision',
    description: 'Understanding images you share.',
    icon: Eye,
  },
  reasoning: {
    label: 'Reasoning',
    description: 'Multi-step planning and harder problem solving.',
    icon: Lightbulb,
  },
  embeddings: {
    label: 'Embeddings',
    description: "Powers Cozmo's memory and search — turns text into something it can compare and recall.",
    icon: Fingerprint,
  },
  tools: {
    label: 'Tools',
    description: 'Can call tools — read files, run commands, use connectors — as part of a response.',
    icon: Wrench,
  },
}
