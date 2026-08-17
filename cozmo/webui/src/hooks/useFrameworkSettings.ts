import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchSchema,
  fetchDiscovery,
  fetchFrameworkConfig,
  setSetting,
  installModel,
  deleteModel,
  saveWorkloadSelection as saveWorkloadSelectionApi,
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
  const [values, setValues] = useState<SettingValues>({})
  const [loading, setLoading] = useState(true)
  const [installs, setInstalls] = useState<Record<string, { phase: string; pct: number | null }>>({})
  const wsRef = useRef<WebSocket | null>(null)

  const load = useCallback(async () => {
    try {
      const [sch, disc, cfg] = await Promise.all([fetchSchema(), fetchDiscovery(), fetchFrameworkConfig()])
      setSchema(sch)
      setDiscovery(disc)
      const v: SettingValues = {}
      for (const s of sch.settings) {
        const cur = readPath(cfg as unknown as Record<string, unknown>, s.id)
        v[s.id] = cur ?? s.default
      }
      setValues(v)
    } catch {
      showError("Couldn't load settings. Is Cozmo's backend running?")
    } finally {
      setLoading(false)
    }
  }, [showError])

  useEffect(() => { void load() }, [load])

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
          // Advisory recommendations refreshed on the backend — re-read config
          // + discovery. Selection is never rewritten by this event.
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
  }, [])

  // Remove a model from disk. Deletion never changes workload selections —
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

  // Persist the user's workload -> model selection verbatim. The backend never
  // auto-populates selection; recommendations are advisory only.
  const saveWorkloadSelection = useCallback(async (workloads: Record<string, string>) => {
    const res = await saveWorkloadSelectionApi(workloads)
    if (res.ok) {
      await load()
    } else {
      showError(res.error ?? "Couldn't save model selection.")
    }
    return res
  }, [load, showError])

  // Explicit "Use Recommended": apply advisory recommendations as the
  // selection. An optional workloads list limits the apply; other workloads
  // keep their current selection. Never installs anything.
  const applyRecommended = useCallback(async (workloads?: string[]) => {
    const res = await applyRecommendedModelsApi(workloads)
    if (res.ok) {
      await load()
    } else {
      showError(res.error ?? "Couldn't apply recommended models.")
    }
    return res
  }, [load, showError])

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
    settingsByCategory,
    loading,
    installs,
    set,
    install,
    removeModel,
    refreshDiscovery,
    dismissRecommended,
    saveWorkloadSelection,
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