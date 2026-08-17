import { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Eye,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react'
import type { CapabilityEvidence, DiscoveryHardware, DiscoveryPayload, DiscoveredModelEntry, RecommendationExplanation, SchemaResponse, WorkloadRecommendation } from './api'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { workloadsFromDiscovery } from './workloads'

interface Props {
  discovery: DiscoveryPayload | null
  schema: SchemaResponse | null
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onDelete: (name: string) => Promise<boolean>
  onDismiss?: (name: string) => Promise<boolean>
  onRefresh: () => Promise<void>
  loading: boolean
  onSaveSelection: (workloads: Record<string, string>) => Promise<{ ok: boolean; error?: string }>
  onApplyRecommended: (workloads?: string[]) => Promise<{ ok: boolean; error?: string }>
}

/**
 * Models — workload-based configuration surface.
 *
 * Every workload (general / research / code) has an explicit selection the user
 * controls. Recommendations from the backend are strictly advisory: nothing is
 * selected or installed until the user acts — either through "Use Recommended"
 * or by picking models directly. Selections are persisted verbatim and never
 * rewritten by the backend.
 */
export function ModelsSettings({ discovery, schema, installing, onInstall, onDelete, onDismiss, onRefresh, loading, onSaveSelection, onApplyRecommended }: Props) {
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [applying, setApplying] = useState(false)
  const [stateError, setStateError] = useState<string | null>(null)
  const [expandedWhy, setExpandedWhy] = useState<string | null>(null)

  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  // Workload keys/labels/descriptions come from the backend schema + discovery
  // payload — the frontend never hardcodes the workload universe.
  const WORKLOADS = workloadsFromDiscovery(discovery, schema)

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

  const useRecommendedFor = async (wk: string) => {
    setApplying(true)
    setStateError(null)
    const res = await onApplyRecommended([wk])
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
  const availableCount = discovery.models.filter((m) => m.status === 'available').length

  // Recommended-but-missing models Cozmo would prefer, unless explicitly
  // declined. Embeddings are never surfaced here; the user-facing capability
  // set is backend-defined and not exposed in the payload, so the filter is
  // kept as the backend's advisory surface (see Phase 6 Task 7 report).
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
          workloadMeta={WORKLOADS}
          installedNames={discovery.installedNames}
          hardware={discovery.hardware}
          provisional={discovery.recommended?.provisional ?? false}
          applying={applying}
          onUseRecommended={useRecommended}
          onUseRecommendedFor={useRecommendedFor}
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
          subtitle="The model Cozmo uses for each workload. Changes save immediately. Recommendations are advisory — Cozmo never changes your selection on its own; your choice stays yours until you change it."
        />
        <div className="space-y-2">
          {WORKLOADS.map((w) => {
            const model = selection[w.key] ?? ''
            const entry = modelByName(discovery, model)
            const missing = model !== '' && !discovery.installedNames.includes(model)
            const vision = w.key === 'general' && discovery.vision_capable
            const rec = discovery.recommended?.workloads?.[w.key] ?? null
            const recommended = rec?.model ?? ''
            const explanation = rec?.explanation ?? null
            return (
              <SelectionRow
                key={w.key}
                workload={w}
                model={model}
                entry={entry}
                installedModels={installedModels}
                missing={missing}
                vision={vision}
                recommended={recommended}
                recommendation={rec}
                explanation={explanation}
                expanded={expandedWhy === w.key}
                onToggleWhy={() => setExpandedWhy(expandedWhy === w.key ? null : w.key)}
                saving={saving}
                applying={applying}
                onSelect={onSelect}
                onUseRecommended={() => useRecommendedFor(w.key)}
              />
            )
          })}
        </div>
      </section>

      {/* 3. Model Library */}
      <section>
        <SectionHeader
          title="Model library"
          subtitle="Models installed on this machine plus curated candidates Cozmo can recommend. Not an exhaustive catalog. Install is explicit — nothing happens until you choose."
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
          {availableCount > 0 && (
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-sky-500" /> {availableCount} available to install</span>
          )}
          {missingCount > 0 && (
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" /> {missingCount} missing</span>
          )}
        </div>

        <div className="space-y-2">
          {rows.map((m) => (
            <ModelRow key={m.name} model={m} install={installing[m.name]} onInstall={onInstall} onDelete={onDelete} />
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

function RecommendedModels({ recs, workloadMeta, installedNames, hardware, provisional, applying, onUseRecommended, onUseRecommendedFor }: {
  recs: { workload: string; model: string; reasons: string[]; caveats: string[]; qualification: string; hardwareConfidence: string; visionCapable: boolean }[]
  workloadMeta: { key: string; label: string; desc: string }[]
  installedNames: string[]
  hardware: DiscoveryHardware
  provisional: boolean
  applying: boolean
  onUseRecommended: () => void
  onUseRecommendedFor: (workload: string) => void
}) {
  const labelOf = (wk: string) => workloadMeta.find((w) => w.key === wk)?.label ?? wk
  const hardwareRam = hardware.ramGb ?? 0
  const gpuName = hardware.gpu?.name ?? ''
  const vramGb = hardware.gpu?.vramTotalGb ?? null
  const confToken = hardware.confidence ?? 'unknown'
  const confLabel = { high: 'High', medium: 'Medium', low: 'Low', unknown: 'Unknown' }[confToken] ?? confToken
  return (
    <section aria-label="Recommended models">
      <div className="p-4 rounded-xl border border-sky-500/30 bg-sky-500/5 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="text-sky-400 shrink-0" />
            <div>
              <p className="text-sm font-medium text-base-100">Recommended models</p>
              <p className="text-[11px] text-base-500 leading-relaxed mt-0.5">
                Suggestions computed by Cozmo from your hardware and installed models.
                Advisory only — nothing changes until you choose.
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
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 pt-2 mt-1 border-t border-sky-500/15">
          <HardwareFact label="GPU" value={gpuName || 'Unknown'} unknown={!gpuName} />
          <HardwareFact label="VRAM" value={vramGb == null ? 'Unknown' : `${vramGb} GB`} unknown={vramGb == null} />
          <HardwareFact label="System RAM" value={hardwareRam > 0 ? `${hardwareRam} GB` : 'Unknown'} unknown={hardwareRam <= 0} />
          <HardwareFact label="Detection confidence" value={confLabel} unknown={confToken === 'unknown'} />
        </div>
        <p className="text-[10px] text-base-500 leading-relaxed">
          These are Cozmo&apos;s suggestions — you control the actual selected model. &ldquo;Using recommended&rdquo; only
          compares what you&apos;ve selected with what Cozmo suggests. A recommendation changing never changes your
          selection; only the &ldquo;Use Recommended&rdquo; action does.
        </p>
        {provisional && (
          <div data-provisional className="flex items-center gap-1.5 text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-1.5 rounded-lg">
            <AlertTriangle size={11} className="shrink-0" />
            <span>Recommendations are based on partial hardware information — some hardware details are unknown.</span>
          </div>
        )}
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
                  <button
                    onClick={() => onUseRecommendedFor(r.workload)}
                    disabled={applying}
                    data-workload={r.workload}
                    title={`Use the recommended model for ${label}`}
                    className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg bg-accent/10 border border-accent/30 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
                  >
                    {applying ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
                    {applying ? 'Applying…' : 'Use Recommended'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

function HardwareFact({ label, value, unknown }: { label: string; value: string; unknown?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-base-400">
      <span className="text-base-500">{label}:</span>
      <span data-hardware-fact={label} className={unknown ? 'italic text-base-500' : 'text-base-200'}>
        {value}
      </span>
    </span>
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

function SelectionRow({ workload, model, entry, installedModels, missing, vision, recommended, recommendation, explanation, expanded, onToggleWhy, saving, applying, onSelect, onUseRecommended }: {
  workload: { key: string; label: string; desc: string }
  model: string
  entry?: DiscoveredModelEntry
  installedModels: DiscoveredModelEntry[]
  missing: boolean
  vision: boolean
  recommended: string
  recommendation: WorkloadRecommendation | null
  explanation: RecommendationExplanation | null
  expanded: boolean
  onToggleWhy: () => void
  saving: boolean
  applying: boolean
  onSelect: (workload: string, model: string) => void
  onUseRecommended: () => void
}) {
  const usingRecommended = !!model && !!recommended && model === recommended
  const recommendationChanged = !!model && !!recommended && model !== recommended
  return (
    <>
      <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm text-base-100 font-medium">{workload.label}</p>
            {usingRecommended && (
              <span className="flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                <CheckCircle2 size={10} /> Using recommended
              </span>
            )}
            {recommendationChanged && (
              <span className="flex items-center gap-1 text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-1.5 py-0.5 rounded">
                <AlertTriangle size={10} /> Recommendation changed
              </span>
            )}
            {vision && (
              <span className="flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                <Eye size={10} /> Vision-capable
              </span>
            )}
          </div>
          <p className="text-[11px] text-base-500 mt-0.5">{workload.desc}</p>
          {recommended && (
            <p className="flex items-center gap-1 text-[11px] text-sky-400 mt-1">
              <Sparkles size={11} className="shrink-0" /> Recommended: <span className="font-mono">{recommended}</span>
            </p>
          )}
          {model && (
            <p className={`text-[10px] mt-1 font-mono truncate ${missing ? 'text-amber-400' : 'text-base-400'}`}>
              {missing
                ? `"${model}" is not installed — Cozmo cannot use it until it is.`
                : entry?.displayName ?? model}
            </p>
          )}
          {recommended && explanation && (
            <button
              onClick={onToggleWhy}
              data-why-workload={workload.key}
              title={`Why is ${recommended} recommended for ${workload.label}?`}
              className="text-[10px] text-base-500 hover:text-base-300 underline underline-offset-2 mt-1 transition-colors"
            >
              {expanded ? 'Why this model? ▴' : 'Why this model? ▾'}
            </button>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          {recommended && !usingRecommended && (
            <button
              onClick={onUseRecommended}
              disabled={applying || saving}
              data-workload={workload.key}
              title={`Use the recommended model for ${workload.label}`}
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg bg-accent/10 border border-accent/30 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {applying ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
              {applying ? 'Applying…' : 'Use Recommended'}
            </button>
          )}
          {!recommended && (
            <p className="text-[10px] text-base-500">No recommendation available</p>
          )}
          <label>
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
              {model && !installedModels.some((m) => m.name === model) && (
                <option value={model} disabled>{entry?.displayName ?? model}</option>
              )}
            </select>
          </label>
        </div>
      </div>
      {expanded && explanation && recommendation && (
        <RecommendationExplanation
          workloadLabel={workload.label}
          recommendation={recommendation}
          selected={model}
        />
      )}
    </>
  )
}

const PROVENANCE_LABELS: Record<string, string> = {
  runtime: 'Runtime evidence',
  seed: 'Curated evidence',
  'name-inference': 'Name-based hint',
  reported: 'Reported evidence',
}

const PROVENANCE_TONES: Record<string, string> = {
  runtime: 'text-emerald-400',
  seed: 'text-sky-400',
  'name-inference': 'text-amber-400',
}

const ALT_STRENGTH_LABELS: Record<string, string> = {
  runtime: 'Runtime evidence',
  'trusted-seed': 'Trusted curated evidence',
  'supported-seed': 'Curated evidence',
  reported: 'Reported evidence',
  'experimental-seed': 'Experimental / unverified',
  'name-inference': 'Name-based hint',
}

const REC_CONF_LABELS: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  unknown: 'Unknown',
}

function RecommendationExplanation({ workloadLabel, recommendation, selected }: {
  workloadLabel: string
  recommendation: WorkloadRecommendation
  selected: string
}) {
  const explanation = recommendation.explanation
  const prov = explanation?.provenance ?? null
  const hw = explanation?.hardwareFit ?? null
  const sourceLabel = prov?.source
    ? (PROVENANCE_LABELS[prov.source] ?? `Evidence: ${prov.source}`)
    : 'Unknown'
  const sourceTone = PROVENANCE_TONES[prov?.source ?? ''] ?? 'text-base-400'
  const weak = prov?.source === 'name-inference'
  const fitLabel = hw ? (HARDWARE_FIT_LABELS[hw.fit] ?? hw.fit) : 'Hardware fit unknown'
  const fitTone = hw?.fit === 'fits' ? 'text-emerald-400'
    : hw?.fit === 'does_not_fit' ? 'text-red-400' : 'text-base-400'
  const confLabel = hw?.confidence
    ? (HARDWARE_CONFIDENCE_LABELS[hw.confidence] ?? hw.confidence)
    : ''
  const alternatives = explanation?.alternatives ?? []
  const capDot = weak ? 'bg-amber-500'
    : prov?.source === 'runtime' ? 'bg-emerald-500'
    : prov?.source === 'seed' ? 'bg-sky-500' : 'bg-base-500'
  return (
    <div
      data-why-panel
      aria-label={`Why this model for ${workloadLabel}`}
      className="ml-3 mt-1 p-3 rounded-lg bg-base-900/60 border border-base-700 space-y-2"
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-xs font-medium text-base-200">Why this model?</p>
        <p className="text-[10px] text-base-500">
          Recommended: <span className="font-mono text-sky-400">{recommendation.model}</span>
          {selected && selected !== recommendation.model && (
            <> · Selected: <span className="font-mono text-base-300">{selected}</span></>
          )}
        </p>
      </div>
      {recommendation.hardwareConfidence && (
        <p data-recommendation-confidence className="flex items-center gap-1.5 text-[11px] text-base-400">
          <ShieldCheck size={11} className="text-base-500 shrink-0" />
          Recommendation confidence: {REC_CONF_LABELS[recommendation.hardwareConfidence] ?? recommendation.hardwareConfidence}
        </p>
      )}
      {explanation?.provisional && (
        <div className="flex items-center gap-1.5 text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-1 rounded">
          <AlertTriangle size={11} className="shrink-0" />
          <span>Provisional recommendation — some hardware/model information is uncertain.</span>
        </div>
      )}
      <div className="text-[11px] space-y-1">
        <p className="text-[10px] uppercase tracking-wide text-base-500">Capability</p>
        <p className="flex items-center gap-1.5 text-base-300">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${capDot}`} />
          <span className="capitalize">{recommendation.capability}</span>
          <span className={sourceTone}>— {sourceLabel}</span>
          {weak && <span className="text-base-500 italic">(weak)</span>}
        </p>
      </div>
      {hw && (
        <div className="text-[11px] space-y-1">
          <p className="text-[10px] uppercase tracking-wide text-base-500">Hardware</p>
          <p className={`flex items-center gap-1.5 ${fitTone}`}>
            {fitLabel}
            {confLabel && <span className="text-base-500">· {confLabel}</span>}
          </p>
          {hw.strength === 'weak' && hw.basis.length > 0 && (
            <p className="text-[10px] text-base-500">Estimated from {hw.basis.join(', ')}.</p>
          )}
          {hw.fit === 'unknown' && (
            <p className="text-[10px] text-base-500">
              Recommendation is provisional because hardware information is incomplete.
            </p>
          )}
        </div>
      )}
      <div className="text-[11px] space-y-1">
        <p className="text-[10px] uppercase tracking-wide text-base-500">Qualification</p>
        <QualificationBadge qualification={recommendation.qualification || undefined} />
      </div>
      {recommendation.reasons.length > 0 && (
        <div className="text-[11px] space-y-1">
          <p className="text-[10px] uppercase tracking-wide text-base-500">Why it scored well</p>
          <ul className="space-y-0.5">
            {recommendation.reasons.map((r) => (
              <li key={r} className="flex items-center gap-1.5 text-base-400">
                <Sparkles size={10} className="text-accent shrink-0" /> {r}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="text-[11px] space-y-1">
        <p className="text-[10px] uppercase tracking-wide text-base-500">Other good options</p>
        {alternatives.length === 0 ? (
          <p className="text-base-500">No other verified alternatives found.</p>
        ) : (
          <ul className="space-y-1">
            {alternatives.map((alt) => (
              <li key={alt.model} className="flex items-center gap-1.5 text-base-300 flex-wrap">
                <span className="font-mono">{alt.model}</span>
                <span className="text-base-500">·</span>
                <span className={alt.fit === 'fits' ? 'text-emerald-400'
                  : alt.fit === 'does_not_fit' ? 'text-red-400' : 'text-base-500'}>
                  {HARDWARE_FIT_LABELS[alt.fit] ?? alt.fit}
                </span>
                <span className="text-base-500">·</span>
                <span className={PROVENANCE_TONES[alt.strength] ?? 'text-base-400'}>
                  {ALT_STRENGTH_LABELS[alt.strength] ?? alt.strength}
                  {alt.strength === 'name-inference' ? ' (weak)' : ''}
                </span>
                {alt.qualification && (
                  <QualificationBadge qualification={alt.qualification} />
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
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

function ModelRow({ model, install, onInstall, onDelete }: {
  model: DiscoveredModelEntry
  install?: { phase: string; pct: number | null }
  onInstall: (name: string) => Promise<boolean>
  onDelete: (name: string) => Promise<boolean>
}) {
  const installed = model.status === 'installed'
  const busy = install && install.phase !== 'done'
  const fit = model.eligibility?.hardwareFit
  // Backend verdict is authoritative: does_not_fit disables installation.
  // unknown never blocks; installed/missing are never installable via this row.
  const blockedByHardware = !installed && fit === 'does_not_fit'
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const confirmRemove = async () => {
    setDeleting(true)
    const ok = await onDelete(model.name)
    setDeleting(false)
    if (ok) setConfirmingDelete(false)
  }
  return (
    <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm text-base-100 font-mono truncate">{model.displayName}</p>
          <StatusBadge status={model.status} />
          {model.qualification && <QualificationBadge qualification={model.qualification} />}
        </div>
        {model.stale && (
          <p className="flex items-center gap-1 text-[10px] text-amber-400 mt-1">
            <AlertTriangle size={10} className="shrink-0" /> Stale inventory — showing cached model information
          </p>
        )}
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
        {fit && (
          <div className="flex flex-wrap items-center gap-1 mt-1">
            <HardwareFitBadge fit={fit} confidence={model.eligibility?.hardwareConfidence} />
          </div>
        )}
        <CapabilityEvidenceList evidence={model.capabilityEvidence} />
        {(model.caveats?.length ?? 0) > 0 && (
          <div className="flex flex-wrap items-center gap-1 mt-1">
            {model.caveats?.map((c) => (
              <span key={c} className="flex items-center gap-1 text-[10px] text-amber-400">
                <AlertTriangle size={9} /> {c}
              </span>
            ))}
          </div>
        )}
        {blockedByHardware && (
          <p className="flex items-center gap-1 text-[10px] text-red-400 mt-1">
            <AlertTriangle size={10} className="shrink-0" /> Not recommended for the detected hardware.
          </p>
        )}
        {install && (
          <p className="text-[11px] text-base-500 mt-1">
            {install.phase === 'done' ? 'Install complete' : `${install.phase}${install.pct != null ? ` — ${install.pct}%` : ''}`}
          </p>
        )}
      </div>
      {!installed && !blockedByHardware && (
        <button
          onClick={() => void onInstall(model.name)}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-60 shrink-0"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          {busy ? (install.phase === 'done' ? 'Installed' : 'Installing…') : 'Install'}
        </button>
      )}
      {installed && !confirmingDelete && (
        <button
          onClick={() => setConfirmingDelete(true)}
          title={`Remove ${model.name}`}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-base-700 text-base-400 hover:text-red-400 hover:border-red-500/40 transition-colors shrink-0"
        >
          <Trash2 size={13} /> Remove
        </button>
      )}
      {installed && confirmingDelete && (
        <div data-remove-confirm className="flex flex-col items-end gap-2 shrink-0 max-w-xs">
          <p className="text-xs font-medium text-base-100">Remove {model.name}?</p>
          <p className="text-[10px] text-base-500 leading-relaxed text-right">
            This will remove the model from your device. It will not change your workload selections.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setConfirmingDelete(false)}
              disabled={deleting}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-base-700 text-base-400 hover:text-base-200 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => void confirmRemove()}
              disabled={deleting}
              title={`Confirm remove ${model.name}`}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-red-500/15 border border-red-500/40 text-red-400 hover:bg-red-500/25 transition-colors disabled:opacity-50"
            >
              {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              {deleting ? 'Removing…' : 'Remove'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const HARDWARE_FIT_LABELS: Record<string, string> = {
  fits: 'Fits detected hardware',
  does_not_fit: 'Does not fit current hardware',
  unknown: 'Hardware fit unknown',
}

const HARDWARE_CONFIDENCE_LABELS: Record<string, string> = {
  high: 'high confidence',
  medium: 'medium confidence',
  low: 'low confidence',
  unknown: '',
}

// Capability-evidence provenance labels. Runtime is measured and strongest;
// seed/curated is advisory-but-curated; name inference is a weak hint and is
// never presented as confirmed support.
const EVIDENCE_SOURCE_LABELS: Record<string, string> = {
  runtime: 'Runtime evidence',
  seed: 'Curated evidence',
  'name-inference': 'Name-based hint',
  reported: 'Reported evidence',
}

function HardwareFitBadge({ fit, confidence }: { fit: string; confidence?: string }) {
  const confLabel = HARDWARE_CONFIDENCE_LABELS[confidence ?? ''] ?? ''
  const tone =
    fit === 'fits' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
    : fit === 'does_not_fit' ? 'text-red-400 bg-red-500/10 border-red-500/20'
    : 'text-base-400 bg-base-800 border-base-700'
  const icon =
    fit === 'fits' ? <CheckCircle2 size={10} />
    : fit === 'does_not_fit' ? <AlertTriangle size={10} />
    : <Cpu size={10} />
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${tone}`}>
      {icon}
      {HARDWARE_FIT_LABELS[fit] ?? fit}
      {confLabel && <span className="opacity-70">· {confLabel}</span>}
    </span>
  )
}

const QUALIFICATION_TONES: Record<string, string> = {
  trusted: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  supported: 'text-sky-400 bg-sky-500/10 border-sky-500/20',
  experimental: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  incompatible: 'text-red-400 bg-red-500/10 border-red-500/20',
}

function QualificationBadge({ qualification }: { qualification?: string }) {
  if (!qualification) return null
  const label = qualification.charAt(0).toUpperCase() + qualification.slice(1)
  const tone = QUALIFICATION_TONES[qualification] ?? 'text-base-400 bg-base-800 border-base-700'
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border capitalize ${tone}`}>
      {label}
    </span>
  )
}

function CapabilityEvidenceList({ evidence }: { evidence?: CapabilityEvidence[] }) {
  if (!evidence || evidence.length === 0) return null
  return (
    <div className="mt-1.5 space-y-0.5">
      {evidence.map((e) => {
        const isRuntime = e.source === 'runtime'
        const isSeed = e.source === 'seed'
        const isNameInference = e.source === 'name-inference'
        const sourceLabel = EVIDENCE_SOURCE_LABELS[e.source] ?? (e.source ? `Evidence: ${e.source}` : '')
        const dot = isRuntime ? 'bg-emerald-500' : isSeed ? 'bg-sky-500' : isNameInference ? 'bg-amber-500' : 'bg-base-500'
        const name = e.capability.charAt(0).toUpperCase() + e.capability.slice(1)
        return (
          <p key={e.capability} className="flex items-center gap-1.5 text-[10px] text-base-400">
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
            <span className="font-medium text-base-300">{name}</span>
            <span className={isNameInference ? 'text-amber-400' : 'text-base-500'}>
              — {sourceLabel}
            </span>
            {isNameInference && <span className="text-base-500 italic">(weak)</span>}
            {e.supported === false && <span className="text-red-400">not supported</span>}
            {(e.supported === null || e.supported === undefined) && (
              <span className="text-amber-400">Unknown</span>
            )}
          </p>
        )
      })}
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
