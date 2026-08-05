import type { ModelTier } from './types'

// How a model's tier is explained to the user — building confidence rather
// than just slapping a label on it, per the product direction: supported
// models should say *why* they're trusted, not just that they are.
export const TIER_INFO: Record<ModelTier, { label: string; reasons: string[] }> = {
  supported: {
    label: 'Recommended',
    reasons: ['Tested with Cozmo', 'Works with memory', 'Supports tool calling where applicable'],
  },
  experimental: {
    label: 'Untested',
    reasons: ['May work, but has not been verified', 'Some features may not function correctly', 'Not officially supported'],
  },
}
