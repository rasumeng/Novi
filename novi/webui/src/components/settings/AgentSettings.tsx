import { useEffect, useState } from 'react'
import { Thermometer, ListOrdered, MessageSquareText, UserRound, Cpu, RefreshCw } from 'lucide-react'
import type { SettingsData } from './types'
import type { AgentConfig } from '@/types'
import { fetchKnowledgeOverview } from '@/services/novi'
import type { KnowledgeOverview } from '@/types'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'

interface Props {
  config: SettingsData | null
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
}

/**
 * Agent — identity visibility + autonomy/behavior controls.
 *
 * Brain owns identity evolution; this page only exposes the derived,
 * read-only context projection. There is deliberately no personality
 * selector here — personality is not a user-configurable static field.
 */
export function AgentSettings({ config, setConfig, setDirty }: Props) {
  const agentCfg: AgentConfig = {
    system_prompt: (config as any)?.agent?.system_prompt ?? '',
    max_steps: (config as any)?.agent?.max_steps ?? 10,
    temperature: (config as any)?.agent?.temperature ?? 0.2,
  }

  const agentModel = (config?.models as Record<string, string>)?.['agent'] ?? ''

  const updateAgent = (patch: Partial<AgentConfig>) => {
    if (!config) return
    const agent = { ...((config as any).agent ?? {}), ...patch }
    setConfig({ ...config, agent } as SettingsData)
    setDirty(true)
  }

  return (
    <div className="space-y-6">
      <IdentityContext />

      <div>
        <p className="text-xs text-base-500 mb-2">How Novi behaves when working autonomously. Changes save automatically.</p>
        <div className="space-y-2">
          <div className="p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div className="flex items-center gap-2 mb-2">
              <MessageSquareText size={14} className="text-accent" />
              <p className="text-sm text-base-100 font-medium">Extra instructions</p>
            </div>
            <p className="text-xs text-base-500 mb-2">Additional guidance Novi follows whenever it's working autonomously.</p>
            <textarea
              value={agentCfg.system_prompt ?? ''}
              onChange={(e) => updateAgent({ system_prompt: e.target.value })}
              placeholder="e.g. Always ask the user before destructive operations..."
              rows={3}
              className="w-full bg-base-900 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 resize-none"
            />
          </div>

          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div className="flex items-center gap-2">
              <ListOrdered size={14} className="text-accent" />
              <div>
                <p className="text-sm text-base-100 font-medium">Max steps</p>
                <p className="text-xs text-base-500">How many actions Novi can take in a row before it stops to check in</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={1}
                max={30}
                value={agentCfg.max_steps ?? 10}
                onChange={(e) => updateAgent({ max_steps: parseInt(e.target.value) })}
                className="w-24 h-1.5 rounded-full appearance-none bg-base-700 accent-accent cursor-pointer"
              />
              <span className="text-xs text-base-200 font-mono w-6 text-right">{agentCfg.max_steps}</span>
            </div>
          </div>

          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div className="flex items-center gap-2">
              <Thermometer size={14} className="text-accent" />
              <div>
                <p className="text-sm text-base-100 font-medium">Temperature</p>
                <p className="text-xs text-base-500">Lower = more deterministic, higher = more creative</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round((agentCfg.temperature ?? 0.2) * 100)}
                onChange={(e) => updateAgent({ temperature: parseInt(e.target.value) / 100 })}
                className="w-24 h-1.5 rounded-full appearance-none bg-base-700 accent-accent cursor-pointer"
              />
              <span className="text-xs text-base-200 font-mono w-8 text-right">{agentCfg.temperature?.toFixed(2)}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="p-3 rounded-xl bg-base-800/50 border border-base-700">
        <div className="flex items-center gap-2 mb-1">
          <Cpu size={14} className="text-accent" />
          <p className="text-sm text-base-100 font-medium">Effective model</p>
        </div>
        <p className="text-xs text-base-500 mb-2">
          The model Novi uses for autonomous work. Reference only — change the assignment on the Models page.
        </p>
        <div className="flex items-center justify-between">
          <span className="text-sm text-base-200 font-mono">{agentModel || 'Not set'}</span>
          {!agentModel && <span className="text-[10px] text-base-500">Pick a model on the Models page</span>}
        </div>
      </div>
    </div>
  )
}

/**
 * Read-only identity/context projection supplied by the Brain.
 *
 * This reuses the existing "/api/knowledge/overview" seam — Brain owns the
 * context evolution; this surface only displays the derived view. It never
 * authors identity through a personality selector.
 */
function IdentityContext() {
  const [overview, setOverview] = useState<KnowledgeOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    fetchKnowledgeOverview()
      .then((data) => {
        if (alive) {
          setOverview(data)
          setLoading(false)
        }
      })
      .catch(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [refreshKey])

  if (loading) {
    return (
      <div className="p-4 rounded-2xl border border-base-700 bg-base-800/40">
        <LoadingSkeleton rows={2} compact />
      </div>
    )
  }

  const entries = synopsis(overview)

  return (
    <div className="p-4 rounded-2xl border border-base-700 bg-base-800/40">
      <div className="flex items-center gap-2 mb-1">
        <div className="w-9 h-9 rounded-xl bg-accent/15 text-accent flex items-center justify-center shrink-0">
          <UserRound size={17} />
        </div>
        <div className="flex-1">
          <p className="text-sm font-semibold text-base-100">What Novi knows about you</p>
          <p className="text-xs text-base-500">
            Derived context, updated as you interact. Read-only — sourced from Novi's memory.
          </p>
        </div>
        <button
          onClick={() => setRefreshKey((k) => k + 1)}
          className="p-1.5 rounded-lg text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors"
          title="Refresh"
          aria-label="Refresh identity context"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {entries.length === 0 ? (
        <p className="text-xs text-base-500 mt-3">
          Novi hasn't derived much context yet. Keep talking — this fills in as Novi learns your preferences.
        </p>
      ) : (
        <div className="space-y-2 mt-3">
          {entries.map((c) => (
            <div key={c.label}>
              <p className="text-[11px] font-medium text-base-400 mb-1">{c.label}</p>
              <div className="space-y-1">
                {c.items.slice(0, 5).map((item, i) => (
                  <p key={i} className="text-xs text-base-200 leading-relaxed px-3 py-1.5 rounded-lg bg-base-900/40 border border-base-700/40">
                    {item}
                  </p>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface SynopsisItem {
  label: string
  items: string[]
}

/** Keep the identity-relevant projection surface concise and readable. */
function synopsis(overview: KnowledgeOverview | null): SynopsisItem[] {
  if (!overview) return []
  const categories = overview.categories ?? []
  const order = ['About you', 'Preferences', 'Goals', 'Abilities']
  const out: SynopsisItem[] = []
  for (const label of order) {
    const cat = categories.find((c) => c.label === label)
    if (cat && cat.entries.length > 0) {
      out.push({ label, items: cat.entries.map((e) => e.content) })
    }
  }
  if (out.length > 0) return out
  // No identity-shaped categories — surface whatever context exists.
  const rest = categories.filter((c) => !order.includes(c.label))
  return rest.slice(0, 3).map((c) => ({ label: c.label, items: c.entries.map((e) => e.content) }))
}
