import { describe, it, expect } from 'vitest'
import {
  detectActiveProfile,
  embeddingModelPatch,
  mergeModelCatalog,
  profileToConfigPatch,
  roleModelPatch,
  summarizePreset,
} from './configLayer'
import { PERFORMANCE_PROFILES } from './profiles'
import { SUPPORTED_MODELS } from './catalog'
import { getPreset } from './presets'
import type { SettingsData } from '@/components/settings/types'

const BASE_CONFIG: SettingsData = {
  models: {},
  runtime: { lightweight_mode: false },
  llm: { roles: {} },
}

describe('profileToConfigPatch', () => {
  it('assigns every backend role for the lightweight preset and sets the flag', () => {
    const patch = profileToConfigPatch('lightweight', BASE_CONFIG)
    expect(patch?.runtime?.lightweight_mode).toBe(true)
    expect(patch?.llm?.roles).toMatchObject({
      classifier: { model: 'phi3:mini' },
      router: { model: 'phi3:mini' },
      orchestrator: { model: 'llama3.2:3b' },
      chat: { model: 'llama3.2:3b' },
      coder: { model: 'llama3.2:3b' },
      planner: { model: 'llama3.2:3b' },
      vision: { model: 'llava:7b' },
    })
  })

  it('assigns the balanced preset and clears the lightweight flag', () => {
    const patch = profileToConfigPatch('balanced', { ...BASE_CONFIG, runtime: { lightweight_mode: true } })
    expect(patch?.runtime?.lightweight_mode).toBe(false)
    expect(patch?.llm?.roles?.coder).toMatchObject({ model: 'qwen2.5-coder:7b' })
  })

  it('leaves role assignments untouched for custom and clears the lightweight flag', () => {
    const configWithCustomRoles: SettingsData = {
      ...BASE_CONFIG,
      llm: { roles: { chat: { model: 'some-hand-picked-model' } } },
    }
    const patch = profileToConfigPatch('custom', configWithCustomRoles)
    expect(patch?.runtime?.lightweight_mode).toBe(false)
    expect(patch?.llm).toBeUndefined()
  })
})

describe('detectActiveProfile', () => {
  it('defaults to balanced when there is no config yet', () => {
    expect(detectActiveProfile(null)).toBe('balanced')
  })

  it('returns custom for role assignments that match no known preset', () => {
    const config: SettingsData = { ...BASE_CONFIG, llm: { roles: { chat: { model: 'whatever-the-user-picked' } } } }
    expect(detectActiveProfile(config)).toBe('custom')
  })

  it('round-trips every non-custom profile through profileToConfigPatch', () => {
    for (const profile of PERFORMANCE_PROFILES) {
      if (profile.id === 'custom') continue
      const patch = profileToConfigPatch(profile.id, BASE_CONFIG)
      const configAfter: SettingsData = { ...BASE_CONFIG, ...patch }
      expect(detectActiveProfile(configAfter)).toBe(profile.id)
    }
  })

  it('does not confuse lightweight-flag-off configs that happen to share model ids with a different profile', () => {
    // Same role models as the lightweight preset, but the flag was left off —
    // should not be misreported as the lightweight profile.
    const patch = profileToConfigPatch('lightweight', BASE_CONFIG)
    const configWithFlagOff: SettingsData = { ...BASE_CONFIG, ...patch, runtime: { lightweight_mode: false } }
    expect(detectActiveProfile(configWithFlagOff)).toBe('custom')
  })
})

describe('mergeModelCatalog', () => {
  it('does not duplicate a discovered model that is already in the supported catalog', () => {
    const merged = mergeModelCatalog([{ name: 'llama3.1:8b', provider: 'ollama' }])
    const matches = merged.filter((m) => m.id === 'llama3.1:8b')
    expect(matches).toHaveLength(1)
    expect(matches[0].tier).toBe('supported')
  })

  it('tags unknown discovered models as experimental exactly once each', () => {
    const merged = mergeModelCatalog([
      { name: 'some-random-model:latest', provider: 'ollama' },
      { name: 'some-random-model:latest', provider: 'ollama' },
    ])
    const matches = merged.filter((m) => m.id === 'some-random-model:latest')
    expect(matches).toHaveLength(1)
    expect(matches[0].tier).toBe('experimental')
  })
})

describe('roleModelPatch', () => {
  it('assigns only the target role, leaving other roles and llm fields untouched', () => {
    const config: SettingsData = {
      ...BASE_CONFIG,
      llm: { default_model: 'something', roles: { coder: { model: 'qwen2.5-coder:7b' } } },
    }
    const patch = roleModelPatch(config, 'chat', 'llama3.1:8b')
    expect(patch.llm?.default_model).toBe('something')
    expect(patch.llm?.roles?.chat).toMatchObject({ model: 'llama3.1:8b' })
    expect(patch.llm?.roles?.coder).toMatchObject({ model: 'qwen2.5-coder:7b' })
  })

  it('clears a role when given an empty model id', () => {
    const config: SettingsData = { ...BASE_CONFIG, llm: { roles: { chat: { model: 'llama3.1:8b' } } } }
    const patch = roleModelPatch(config, 'chat', '')
    expect(patch.llm?.roles?.chat).toBeUndefined()
  })

  it('demotes the active profile to custom, since manual overrides are exactly that', () => {
    const preset = profileToConfigPatch('balanced', BASE_CONFIG)
    const balancedConfig: SettingsData = { ...BASE_CONFIG, ...preset }
    expect(detectActiveProfile(balancedConfig)).toBe('balanced')

    const patch = roleModelPatch(balancedConfig, 'chat', 'llama3.1:70b')
    const afterOverride: SettingsData = { ...balancedConfig, ...patch }
    expect(detectActiveProfile(afterOverride)).toBe('custom')
  })
})

describe('embeddingModelPatch', () => {
  it('sets the embedding model without touching llm.roles', () => {
    const config: SettingsData = { ...BASE_CONFIG, llm: { roles: { chat: { model: 'llama3.1:8b' } } } }
    const patch = embeddingModelPatch(config, 'nomic-embed-text')
    expect(patch.embedding?.model).toBe('nomic-embed-text')
    expect((patch as SettingsData).llm).toBeUndefined()
  })
})

describe('summarizePreset', () => {
  it('excludes internal routing roles (classifier/router) from the summary', () => {
    const preset = getPreset('balanced')!
    const summary = summarizePreset(preset, SUPPORTED_MODELS)
    const allUsedFor = summary.flatMap((s) => s.usedFor)
    expect(allUsedFor).not.toContain('Classifier')
    expect(allUsedFor).not.toContain('Router')
    expect(summary.some((s) => s.modelId === 'qwen2.5-coder:7b' && s.usedFor.includes('Coding'))).toBe(true)
  })
})
