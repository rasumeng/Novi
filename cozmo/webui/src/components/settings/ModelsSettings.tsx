import { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Eye,
  Loader2,
  RefreshCw,
  Sparkles,
} from 'lucide-react'
import type { DiscoveryPayload, DiscoveredModelEntry } from './api'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'

interface Props {
  discovery: DiscoveryPayload | null
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onDismiss?: (name: string) => Promise<boolean>
  onRefresh: () => Promise<void>
  loading: boolean
  onSaveSelection: (workloads: Record<string, string>) => Promise<{ ok: boolean; error?: string }>
  onApplyRecommended: () => Promise<{ ok: boolean; error?: string }>
}

/** The three user-facing workloads. Embeddings & internal roles are not shown. */
const WORKLOADS: { key: string; label: string; desc: string }[] = [
  { key: 'general', label: 'General', desc: 'Everyday conversation, planning, and general interaction.' },
  { key: 'research', label: 'Research', desc: 'Deep research, analysis, and long-form reasoning.' },
  { key: 'code', label: 'Code', desc: 'Code generation, editing, and debugging.' },
]

/**
 * Models — workload-based configuration surface.
 *
 * Every workload (general / research / code) has an explicit selection the user
 * controls. Recommendations from the backend are strictly advisory: nothing is
 * selected or installed until the user acts — either through "Use Recommended"
 * or by picking models directly. Selections are persisted verbatim and never
 * rewritten by the backend.
 */
export function ModelsSettings({ discovery, installing, onInstall, onDismiss, onRefresh, loading, onSaveSelection, onApplyRecommended }: Props) {
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [applying, setApplying] = useState(false)
  const [stateError, setStateError] = useState<string | null>(null)

  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const refresh = async () => {
    setRefreshing(true)
    await onRefresh()
    setRefreshing(false)
  }

  const selection = discovery.workloads ?? {}
  const installedModels = discovery.models.filter((m) => m.status === 'installed')

  const onSelect = async (wk: string, model: string) => {
    const next = { ...selection, [wk]: model }
    setSaving(true)
    setStateError(null)
    const res = await onSaveSelection(next)
    if (!res.ok) setStateError(res.error ?? "Couldn't save model selection.")
    setSaving(false)
  }

  const useRecommended = async () => {
    setApplying(true)
    setStateError(null)
    const res = await onApplyRecommended()
    if (!res.ok) setStateError(res.error ?? "Couldn't apply recommended models.")
    setApplying(false)
  }

  const rows = discovery.models
    .filter((m) => m.name.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => {
      const rank = { installed: 0, missing: 1, available: 2 } as Record<string, number>
      return (rank[a.status] ?? 3) - (rank[b.status] ?? 3)
    })

  const missingCount = discovery.missingModels.length
  const installedCount = discovery.models.filter((m) => m.status === 'installed').length

  // Recommended-but-missing models Cozmo would prefer, unless explicitly
  // declined. Embeddings are never surfaced here.
  const missingRecommended = discovery.models.filter(
    (m) =>
      m.status === 'available' &&
      !discovery.dismissedRecommended?.includes(m.name) &&
      Object.keys(m.capabilities ?? {}).some((c) => ['chat', 'reasoning', 'coding', 'vision'].includes(c)),
  )

  const recs = Object.values(discovery.recommended?.workloads ?? {})
  const anyRecommended = recs.length > 0

  return (
    <div className="space-y-6">
      {/* 1. Advisory recommendations */}
      {anyRecommended && (
        <RecommendedModels
          recs={recs}
          installedNames={discovery.installedNames}
          hardwareRam={discovery.hardware.ramGb}
          applying={applying}
          onUseRecommended={useRecommended}
        />
      )}

      {stateError && (
        <div className="flex items-center gap-2 p-3 rounded-xl border border-err/30 bg-err/5">
          <AlertTriangle size={14} className="text-err shrink-0" />
          <p className="text-xs text-err">{stateError}</p>
        </div>
      )}

      {/* Explicit-consent setup for missing recommended models */}
      <RecommendedSetup
        models={missingRecommended}
        installing={installing}
        onInstall={onInstall}
        onDismiss={onDismiss}
      />

      {/* 2. Current selection */}
      <section>
        <SectionHeader
          title="Current selection"
          subtitle="The model Cozmo uses for each workload. Changes save immediately."
        />
        <div className="space-y-2">
          {WORKLOADS.map((w) => {
            const model = selection[w.key] ?? ''
            const entry = modelByName(discovery, model)
            const missing = model !== '' && !discovery.installedNames.includes(model)
            const vision = w.key === 'general' && discovery.vision_capable
            return (
              <SelectionRow
                key={w.key}
                workload={w}
                model={model}
                entry={entry}
                installedModels={installedModels}
                missing={missing}
                vision={vision}
                saving={saving}
                onSelect={onSelect}
              />
            )
          })}
        </div>
      </section>

      {/* 3. Model Library */}
      <section>
        <SectionHeader
          title="Model library"
          subtitle="Live inventory of every model available on this machine. Install from here — changes apply immediately."
        />
        <div className="flex items-center gap-2 mb-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name…"
            aria-label="Filter models"
            className="flex-1 bg-base-900 border border-base-700 rounded-lg px-3 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40"
          />
          <button
            onClick={refresh}
            disabled={refreshing}
            className="p-2 rounded-lg border border-base-700 text-base-400 hover:text-base-200 transition-colors disabled:opacity-50"
            title="Rescan"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-base-500 mb-3">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> {installedCount} installed</span>
          {missingCount > 0 && (
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" /> {missingCount} missing</span>
          )}
        </div>

        <div className="space-y-2">
          {rows.map((m) => (
            <ModelRow key={m.name} model={m} install={installing[m.name]} onInstall={onInstall} />
          ))}
          {rows.length === 0 && (
            <p className="text-xs text-base-500 py-6 text-center">
              {query ? `No models match "${query}".` : 'No models detected. Install one below.'}
            </p>
          )}
        </div>
      </section>
    </div>
  )
}

function modelByName(discovery: DiscoveryPayload, name: string): DiscoveredModelEntry | undefined {
  return name ? discovery.models.find((m) => m.name === name) : undefined
}

// ── 1. Advisory recommendations ──────────────────────────────────────────

function RecommendedModels({ recs, installedNames, hardwareRam, applying, onUseRecommended }: {
  recs: { workload: string; model: string; reasons: string[]; caveats: string[]; qualification: string; hardwareConfidence: string; visionCapable: boolean }[]
  installedNames: string[]
  hardwareRam: number
  applying: boolean
  onUseRecommended: () => void
}) {
  const labelOf = (wk: string) => WORKLOADS.find((w) => w.key === wk)?.label ?? wk
  return (
    <section aria-label="Recommended models">
      <div className="p-4 rounded-xl border border-sky-500/30 bg-sky-500/5 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="text-sky-400 shrink-0" />
            <div>
              <p className="text-sm font-medium text-base-100">Recommended models</p>
              <p className="text-[11px] text-base-500 leading-relaxed mt-0.5">
                Suggestions computed by Cozmo from your hardware ({hardwareRam > 0 ? `${hardwareRam} GB RAM` : 'unknown'}) and
                installed models. Advisory only — nothing changes until you choose.
              </p>
            </div>
          </div>
          <button
            onClick={onUseRecommended}
            disabled={applying}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-60 shrink-0"
          >
            {applying ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {applying ? 'Applying…' : 'Use Recommended'}
          </button>
        </div>
        <div className="space-y-2">
          {recs.map((r) => {
            const installed = installedNames.includes(r.model)
            const label = labelOf(r.workload)
            return (
              <div key={r.workload} className="p-3 rounded-lg bg-base-900/60 border border-base-700">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm text-base-100 font-mono truncate">{label}</p>
                      <span className="text-[10px] text-base-400 bg-base-800 border border-base-700 px-1.5 py-0.5 rounded">
                        {installed ? 'installed' : 'not installed'}
                      </span>
                    </div>
                    <p className="text-sm text-base-200 font-mono truncate mt-1">{r.model}</p>
                    {r.reasons.length > 0 && (
                      <p className="flex items-center gap-1 text-[11px] text-accent mt-1">
                        <Sparkles size={11} /> {r.reasons.join(' · ')}
                      </p>
                    )}
                    {(r.caveats.length > 0 || r.qualification) && (
                      <div className="flex flex-wrap items-center gap-1 mt-1">
                        {r.qualification && (
                          <span className="text-[10px] text-base-400 capitalize">{r.qualification}</span>
                        )}
                        {r.caveats.map((c) => (
                          <span key={c} className="flex items-center gap-1 text-[10px] text-amber-400">
                            <AlertTriangle size={9} /> {c}
                          </span>
                        ))}
                      </div>
                    )}
                    {r.visionCapable && (
                      <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 mt-1">
                        <Eye size={10} /> Vision-capable
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

// ── M3.4 — Recommended model setup (explicit consent) ─────────────────────

function RecommendedSetup({ models, installing, onInstall, onDismiss }: {
  models: DiscoveredModelEntry[]
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onDismiss?: (name: string) => Promise<boolean>
}) {
  if (models.length === 0) return null
  return (
    <section aria-label="Recommended model setup">
      <div className="p-4 rounded-xl border border-sky-500/30 bg-sky-500/5 space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-sky-400 shrink-0" />
          <div>
            <p className="text-sm font-medium text-base-100">Recommended model unavailable</p>
            <p className="text-[11px] text-base-500 leading-relaxed mt-0.5">
              Cozmo would prefer these for your hardware, but they are not installed.
              Installing happens only when you choose — skipping keeps your current eligible models.
            </p>
          </div>
        </div>
        <div className="space-y-2">
          {models.map((m) => {
            const busy = installing[m.name]
            const caps = Object.keys(m.capabilities ?? {}).filter((c) =>
              ['chat', 'reasoning', 'coding', 'vision'].includes(c))
            return (
              <div key={m.name} className="p-3 rounded-lg bg-base-900/60 border border-base-700">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm text-base-100 font-mono truncate">{m.displayName}</p>
                      <StatusBadge status="available" />
                    </div>
                    <div className="flex flex-wrap items-center gap-1 mt-1">
                      {caps.map((c) => (
                        <span key={c} className="text-[10px] text-sky-400 capitalize bg-sky-500/10 border border-sky-500/20 px-1.5 py-0.5 rounded">
                          {c}
                        </span>
                      ))}
                      {m.approxRamGb != null && (
                        <span className="text-[10px] text-base-500">~{m.approxRamGb} GB RAM footprint</span>
                      )}
                    </div>
                    {m.reasons.length > 0 && (
                      <p className="flex items-center gap-1 text-[11px] text-accent mt-1">
                        <Sparkles size={11} /> {m.reasons.join(' · ')}
                      </p>
                    )}
                    {busy && (
                      <p className="text-[11px] text-base-500 mt-1">
                        {busy.phase === 'done' ? 'Install complete' : `${busy.phase}${busy.pct != null ? ` — ${busy.pct}%` : ''}`}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {onDismiss && !busy && (
                      <button
                        onClick={() => void onDismiss(m.name)}
                        className="px-3 py-1.5 text-xs font-medium rounded-lg border border-base-700 text-base-400 hover:text-base-200 hover:border-base-600 transition-colors"
                      >
                        Not now
                      </button>
                    )}
                    <button
                      onClick={() => void onInstall(m.name)}
                      disabled={!!busy}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-60"
                    >
                      {busy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                      {busy ? 'Installing…' : 'Install & use'}
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

// ── 2. Current selection ─────────────────────────────────────────────────

function SelectionRow({ workload, model, entry, installedModels, missing, vision, saving, onSelect }: {
  workload: { key: string; label: string; desc: string }
  model: string
  entry?: DiscoveredModelEntry
  installedModels: DiscoveredModelEntry[]
  missing: boolean
  vision: boolean
  saving: boolean
  onSelect: (workload: string, model: string) => void
}) {
  return (
    <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm text-base-100 font-medium">{workload.label}</p>
          {vision && (
            <span className="flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
              <Eye size={10} /> Vision-capable
            </span>
          )}
        </div>
        <p className="text-[11px] text-base-500 mt-0.5">{workload.desc}</p>
        {model && (
          <p className={`text-[10px] mt-1 font-mono truncate ${missing ? 'text-amber-400' : 'text-base-400'}`}>
            {missing
              ? `"${model}" is not installed — Cozmo cannot use it until it is.`
              : entry?.displayName ?? model}
          </p>
        )}
      </div>
      <label className="shrink-0">
        <span className="sr-only">{workload.label} model</span>
        <select
          disabled={saving}
          value={model}
          onChange={(e) => onSelect(workload.key, e.target.value)}
          title={`Choose the model for ${workload.label}`}
          data-workload={workload.key}
          className="bg-base-900 border border-base-700 rounded-lg px-2 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <option value="">None selected</option>
          {installedModels.map((m) => (
            <option key={m.name} value={m.name}>{m.displayName}</option>
          ))}
        </select>
      </label>
    </div>
  )
}

// ── shared ───────────────────────────────────────────────────────────────

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-2">
      <p className="text-sm text-base-100 font-medium">{title}</p>
      <p className="text-xs text-base-500">{subtitle}</p>
    </div>
  )
}

function ModelRow({ model, install, onInstall }: {
  model: DiscoveredModelEntry
  install?: { phase: string; pct: number | null }
  onInstall: (name: string) => Promise<boolean>
}) {
  const installed = model.status === 'installed'
  const busy = install && install.phase !== 'done'
  return (
    <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm text-base-100 font-mono truncate">{model.displayName}</p>
          <StatusBadge status={model.status} />
        </div>
        {model.reasons.length > 0 && (
          <p className="flex items-center gap-1 text-[11px] text-accent mt-1">
            <Sparkles size={11} /> {model.reasons.join(' · ')}
          </p>
        )}
        {[model.parameterCount, model.quantization, model.family,
          model.contextLength ? `${model.contextLength.toLocaleString()} ctx` : null]
            .filter(Boolean).length > 0 && (
          <p className="text-[10px] text-base-500 font-mono mt-1 truncate">
            {[model.parameterCount, model.quantization, model.family,
              model.contextLength ? `${model.contextLength.toLocaleString()} ctx` : null]
                .filter(Boolean).join(' · ')}
          </p>
        )}
        {(model.qualification || (model.caveats?.length ?? 0) > 0) && (
          <div className="flex flex-wrap items-center gap-1 mt-1">
            {model.qualification && (
              <span className="text-[10px] text-base-400 capitalize">{model.qualification}</span>
            )}
            {model.caveats?.map((c) => (
              <span key={c} className="flex items-center gap-1 text-[10px] text-amber-400">
                <AlertTriangle size={9} /> {c}
              </span>
            ))}
          </div>
        )}
        {install && (
          <p className="text-[11px] text-base-500 mt-1">
            {install.phase === 'done' ? 'Install complete' : `${install.phase}${install.pct != null ? ` — ${install.pct}%` : ''}`}
          </p>
        )}
      </div>
      {!installed && (
        <button
          onClick={() => void onInstall(model.name)}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-60 shrink-0"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          {busy ? (install.phase === 'done' ? 'Installed' : 'Installing…') : 'Install'}
        </button>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: DiscoveredModelEntry['status'] }) {
  if (status === 'installed')
    return (
      <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-[10px] text-emerald-400">
        <CheckCircle2 size={10} /> installed
      </span>
    )
  if (status === 'available')
    return (
      <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-sky-500/10 border border-sky-500/20 text-[10px] text-sky-400">
        available
      </span>
    )
  return (
    <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-400">
      missing
    </span>
  )
}
