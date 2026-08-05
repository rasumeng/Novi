import { useMemo } from 'react'
import type { SettingsData } from '@/components/settings/types'
import {
  detectActiveProfile,
  getPreset,
  getProfile,
  mergeModelCatalog,
  profileToConfigPatch,
  summarizePreset,
  type PresetModelSummary,
} from '@/product/configLayer'
import { PERFORMANCE_PROFILES } from '@/product/profiles'
import type { PerformanceProfileId } from '@/product/types'

interface DiscoveredModel {
  name: string
  provider: string
}

interface Options {
  config: SettingsData | null
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
  discoveredModels: DiscoveredModel[]
}

// The application layer the Settings UI (Phase 1B) will consume: profiles,
// the merged supported/experimental catalog, which profile is active right
// now, and a single action to switch profiles. Everything here reads from
// and writes through the same `config`/`setConfig`/`setDirty` plumbing every
// other settings section already uses — this isn't a parallel state store.
export function useProductConfig({ config, setConfig, setDirty, discoveredModels }: Options) {
  const catalog = useMemo(() => mergeModelCatalog(discoveredModels), [discoveredModels])

  const activeProfileId = useMemo(() => detectActiveProfile(config), [config])
  const activeProfile = getProfile(activeProfileId)
  const activePreset = activeProfile ? getPreset(activeProfile.modelPresetId) : null

  // Which models each profile actually uses, in user-facing terms — computed
  // once per catalog change rather than per card render.
  const profileSummaries = useMemo(() => {
    const map: Partial<Record<PerformanceProfileId, PresetModelSummary[]>> = {}
    for (const profile of PERFORMANCE_PROFILES) {
      const preset = getPreset(profile.modelPresetId)
      map[profile.id] = preset ? summarizePreset(preset, catalog) : []
    }
    return map as Record<PerformanceProfileId, PresetModelSummary[]>
  }, [catalog])

  const applyProfile = (profileId: PerformanceProfileId) => {
    if (!config) return
    const patch = profileToConfigPatch(profileId, config)
    if (!patch) return
    setConfig({ ...config, ...patch })
    setDirty(true)
  }

  return {
    profiles: PERFORMANCE_PROFILES,
    catalog,
    activeProfileId,
    activeProfile,
    activePreset,
    profileSummaries,
    applyProfile,
  }
}
