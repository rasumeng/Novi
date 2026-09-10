import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchSchema,
  fetchDiscovery,
  fetchFrameworkConfig,
  setSetting,
  installModel,
  deleteModel,
  savePrimaryModel as savePrimaryModelApi,
  applyRecommendedModels as applyRecommendedModelsApi,
  dismissRecommendedModel,
  type SchemaResponse,
  type SettingSchema,
  type DiscoveryPayload,
} from '@/components/settings/api'
import { useToast } from '@/hooks/useToast'

export interface SettingValues {
  [id: string]: unknown
}

/**
 * Live, framework-backed settings store. Every change is validated + persisted
 * immediately through PATCH-style endpoints (no Save button). Keeps a local
 * value mirror for instant UI response; reconcile failures surface as toasts.
 */
export function useFrameworkSettings() {
  const { showError } = useToast()
  const [schema, setSchema] = useState<SchemaResponse | null>(null)
  const [discovery, setDiscovery] = useState<DiscoveryPayload | null>(null)
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [values, setValues] = useState<SettingValues>({})
  const [loading, setLoading] = useState(true)
  const [installs, setInstalls] = useState<Record<string, { phase: string; pct: number | null }>>({})
  const wsRef = useRef<WebSocket | null>(null)

  const load = useCallback(async () => {
    try {
      const [sch, disc, cfg] = await Promise.all([fetchSchema(), fetchDiscovery(), fetchFrameworkConfig()])
      setSchema(sch)
      setDiscovery(disc)
      setDiscoveryError(disc.ollamaError ?? (disc.status === 'error' || disc.status === 'degraded' ? `Ollama not reachable at ${disc.ollamaUrl ?? 'http://localhost:11434'}` : null))
      const v: SettingValues = {}
      for (const s of sch.settings) {
        const cur = readPath(cfg as unknown as Record<string, unknown>, s.id)
        v[s.id] = cur ?? s.default
      }
      setValues(v)
    } catch {
      showError("Couldn't load settings. Is Novi's backend running?")
    } finally {
      setLoading(false)
    }
  }, [showError])

  const set = useCallback(async (id: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [id]: value }))
    const ok = await setSetting(id, value)
    if (!ok) showError(`Couldn't save ${id} — change wasn't persisted.`)
    return ok
  }, [showError])

  const install = useCallback(async (name: string) => {
    setInstalls((prev) => ({ ...prev, [name]: { phase: 'queued', pct: 0 } }))
    const res = await installModel(name)
    if (!res.ok) showError(res.error ?? `Couldn't install ${name}.`)
    return res.ok
  }, [showError])

  const refreshDiscovery = useCallback(async () => {
    const disc = await fetchDiscovery()
    setDiscovery(disc)
    setDiscoveryError(disc.ollamaError ?? (disc.status === 'error' || disc.status === 'degraded' ? `Ollama not reachable at ${disc.ollamaUrl ?? 'http://localhost:11434'}` : null))
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Live WebSocket feed for config updates + install progress.
  useEffect(() => {
    const proto = import.meta.env.DEV ? 'ws' : 'wss'
    const base = import.meta.env.DEV ? 'localhost:8765' : window.location.host
    const ws = new WebSocket(`${proto}://${base}/ws/chat`)
    wsRef.current = ws
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'config_updated' && msg.event?.path) {
          setValues((prev) => ({ ...prev, [msg.event.path]: msg.event.value }))
        } else if (msg.type === 'models_resolved') {
          void load()
        } else if (msg.type === 'install_progress' && msg.name) {
          setInstalls((prev) => ({
            ...prev,
            [msg.name]: { phase: msg.status === 'progress' ? msg.phase ?? 'installing' : msg.status, pct: msg.pct ?? null },
          }))
          if (msg.status === 'done' || msg.status === 'error') {
            void load()
          }
        }
      } catch {
        /* ignore malformed frames */
      }
    }
    return () => ws.close()
  }, [load])

  // Background refresh after a selection save. fetchDiscovery returns an
  // all-empty fallback on network error — preserve just-saved primary.
  const refreshDiscoveryPreserving = useCallback(async (preserve?: string) => {
    const disc = await fetchDiscovery()
    setDiscoveryError(disc.ollamaError ?? (disc.status === 'error' || disc.status === 'degraded' ? `Ollama not reachable at ${disc.ollamaUrl ?? 'http://localhost:11434'}` : null))
    const preservedModel = preserve ?? ''
    setDiscovery((d) => {
      if (!d) return disc
      const payloadEmpty = !(disc.primary ?? disc.model ?? '')
      if (preservedModel && payloadEmpty) {
        return { ...disc, primary: preservedModel, model: preservedModel }
      }
      return disc
    })
  }, [])

  // Remove a model from disk. Deletion never changes the primary selection —
  // the model simply disappears from the installed set; if it was selected it
  // becomes a configured-but-missing model. After a successful delete the
  // library is refreshed so installed/missing status is accurate.
  const removeModel = useCallback(async (name: string) => {
    const res = await deleteModel(name)
    if (!res.ok) {
      showError(res.error ?? `Couldn't remove ${name}.`)
      return false
    }
    await refreshDiscovery()
    return true
  }, [refreshDiscovery, showError])

  // M3.4: user declined a recommended-model install ("not now"). Records the
  // choice through the backend so it stays dismissed; the model remains
  // installable from the Model library with a fresh explicit consent.
  const dismissRecommended = useCallback(async (name: string) => {
    const res = await dismissRecommendedModel(name)
    if (!res.ok) {
      showError(res.error ?? `Couldn't dismiss ${name}.`)
      return false
    }
    setDiscovery((d) =>
      d
        ? {
            ...d,
            dismissedRecommended: d.dismissedRecommended?.includes(name)
              ? d.dismissedRecommended
              : [...(d.dismissedRecommended ?? []), name],
          }
        : d,
    )
    return true
  }, [showError])

  // Optimistically reflect a persisted selection in local discovery state.
  const applySelectionLocal = useCallback((model: string | undefined) => {
    if (!model) return
    setDiscovery((d) => {
      if (!d) return d
      return { ...d, primary: model, model }
    })
  }, [])

  // Persist the user's primary model verbatim. Recommendations advisory only.
  const savePrimaryModel = useCallback(async (model: string) => {
    const res = await savePrimaryModelApi(model)
    if (res.ok) {
      applySelectionLocal(model)
      void refreshDiscoveryPreserving(model)
    } else {
      showError((res as { error?: string }).error ?? "Couldn't save model selection.")
    }
    return res
  }, [refreshDiscoveryPreserving, showError, applySelectionLocal])

  // Explicit "Use Recommended": apply advisory primary recommendation.
  const applyRecommended = useCallback(async () => {
    const res = await applyRecommendedModelsApi()
    if (res.ok) {
      const model = (res as { model?: string }).model
        ?? (res as { selection?: { model?: string } }).selection?.model ?? ''
      if (model) {
        applySelectionLocal(model)
        void refreshDiscoveryPreserving(model)
      }
    } else {
      showError((res as { error?: string }).error ?? "Couldn't apply recommended model.")
    }
    return res
  }, [refreshDiscoveryPreserving, showError, applySelectionLocal])

  const settingsByCategory = useMemo(() => {
    const map: Record<string, SettingSchema[]> = {
      general: [],
      models: [],
      agent: [],
      memory: [],
      skills: [],
      connectors: [],
      permissions: [],
      developer: [],
    }
    for (const s of schema?.settings ?? []) {
      map[s.category]?.push(s)
    }
    return map
  }, [schema])

  return {
    schema,
    values,
    discovery,
    discoveryError,
    settingsByCategory,
    loading,
    installs,
    set,
    install,
    removeModel,
    refreshDiscovery,
    dismissRecommended,
    savePrimaryModel,
    applyRecommended,
    reload: load,
  }
}

function readPath(obj: Record<string, unknown>, path: string): unknown {
  let cur: unknown = obj
  for (const part of path.split('.')) {
    if (cur && typeof cur === 'object' && part in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[part]
    } else {
      return undefined
    }
  }
  return cur
}
