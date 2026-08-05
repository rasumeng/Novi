import type { ModelPreset } from './types'

// Structured replacement for hand-picking a model per backend role. This is
// the piece that actually has a real, working write path today — see
// configLayer.profileToConfigPatch, which turns one of these into the
// `llm.roles` object PUT /api/config already accepts.
export const MODEL_PRESETS: ModelPreset[] = [
  {
    id: 'lightweight',
    label: 'Lightweight',
    roleAssignments: {
      classifier: 'phi3:mini',
      router: 'phi3:mini',
      orchestrator: 'llama3.2:3b',
      chat: 'llama3.2:3b',
      coder: 'llama3.2:3b',
      planner: 'llama3.2:3b',
      vision: 'llava:7b',
    },
  },
  {
    id: 'balanced',
    label: 'Balanced',
    roleAssignments: {
      classifier: 'phi3:mini',
      router: 'phi3:mini',
      orchestrator: 'llama3.1:8b',
      chat: 'llama3.1:8b',
      coder: 'qwen2.5-coder:7b',
      planner: 'llama3.1:8b',
      vision: 'llava:7b',
    },
  },
  {
    id: 'high_quality',
    label: 'High Quality',
    roleAssignments: {
      classifier: 'llama3.2:3b',
      router: 'llama3.2:3b',
      orchestrator: 'llama3.1:70b',
      chat: 'llama3.1:70b',
      coder: 'qwen2.5-coder:32b',
      planner: 'llama3.1:70b',
      vision: 'llava:13b',
    },
  },
]

export function getPreset(id: string | null): ModelPreset | null {
  if (!id) return null
  return MODEL_PRESETS.find((p) => p.id === id) ?? null
}
