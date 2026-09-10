import { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Eye,
  Loader2,
  MemoryStick,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react'
import type { CapabilityEvidence, DiscoveryHardware, DiscoveryPayload, DiscoveredModelEntry, RecommendationExplanation, SchemaResponse, PrimaryRecommendation } from './api'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { primaryModelFromDiscovery, primaryRecommendation, embeddingModelFromSchema, isEmbeddingOnly } from './api'
import { CapabilityChips, capsFromEntry } from '@/components/common/CapabilityChips'

interface Props {
  discovery: DiscoveryPayload | null
  schema: SchemaResponse | null
  /** Live framework value for ``embedding.model``; falls back to schema default. */
  embeddingModel?: string
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onDelete: (name: string) => Promise<boolean>
  onDismiss?: (name: string) => Promise<boolean>
  onRefresh: () => Promise<void>
  loading: boolean
  onSaveSelection: (model: string) => Promise<{ ok: boolean; error?: string }>
  onApplyRecommended: () => Promise<{ ok: boolean; error?: string }>
}

/**
 * Models — single primary-model configuration surface.
 *
 * One user-selected model ("Novi Model") powers conversation, coding,
 * research, and agent tasks. Recommendations are strictly advisory.
 */
export function ModelsSettings({ discovery, schema, embeddingModel, installing, onInstall, onDelete, onDismiss, onRefresh, loading, onSaveSelection, onApplyRecommended }: Props) {
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [applying, setApplying] = useState(false)
  const [stateError, setStateError] = useState<string | null>(null)
  const [expandedWhy, setExpandedWhy] = useState<string | null>(null)
  const [showManagement, setShowManagement] = useState(false)

  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const PRIMARY_LABEL = 'Model'
  const PRIMARY_DESC = "This model powers Novi's conversations and tools."

  const refresh = async () => {
    setRefreshing(true)
    await onRefresh()
    setRefreshing(false)
  }

  const primary = primaryModelFromDiscovery(discovery)
  const installedModels = discovery.models.filter((m) => m.status === 'installed')
  // Embedding-only models power Memory, not chat — never offer them as the
  // Novi Model. Unknown-capability entries ({}) stay selectable.
  const chatModels = installedModels.filter((m) => !isEmbeddingOnly(m))
  const embeddingName = embeddingModelFromSchema(schema, embeddingModel)
  const embeddingEntry = embeddingName ? modelByName(discovery, embeddingName) : undefined
  const embeddingInstalled = embeddingName !== '' &&
    (discovery.installedNames.includes(embeddingName) || embeddingEntry?.status === 'installed')

  const onSelect = async (model: string) => {
    setSaving(true)
    setStateError(null)
    const res = await onSaveSelection(model)
    if (!res.ok) setStateError(res.error ?? "Couldn't save model selection.")
    setSaving(false)
  }

  const useRecommended = async () => {
    setApplying(true)
    setStateError(null)
    const res = await onApplyRecommended()
    if (!res.ok) setStateError(res.error ?? "Couldn't apply recommended model.")
    setApplying(false)
  }

  const useRecommendedFor = async () => {
    await useRecommended()
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

  // Recommended-but-missing models Novi would prefer, unless explicitly
  // declined. Embeddings are never surfaced here; the user-facing capability
  // set is backend-defined and not exposed in the payload, so the filter is
  // kept as the backend's advisory surface (see Phase 6 Task 7 report).
  const missingRecommended = discovery.models.filter(
    (m) =>
      m.status === 'available' &&
      !discovery.dismissedRecommended?.includes(m.name) &&
      Object.keys(m.capabilities ?? {}).some((c) => ['chat', 'reasoning', 'coding', 'vision'].includes(c)),
  )

  const singleRec = primaryRecommendation(discovery)
  const provisional = discovery.recommended?.provisional ?? false

  const isUnreachable = discovery.ollamaReachable === false || discovery.status === 'error' || discovery.status === 'degraded'
  const ollamaUrl = discovery.ollamaUrl ?? 'http://localhost:11434'
  const ollamaError = discovery.ollamaError ?? (isUnreachable ? `Ollama not reachable at ${ollamaUrl}` : null)

  return (
    <div className="space-y-4">
      {/* Task 2.1 — honest Ollama discovery failure banner */}
      {isUnreachable && (
        <div data-testid="ollama-unreachable-banner" className="flex items-center justify-between gap-3 p-3 rounded-xl border border-amber-500/30 bg-amber-500/10">
          <p className="flex items-center gap-2 text-xs text-amber-300">
            <AlertTriangle size={14} className="shrink-0" />
            <span>
              {discovery.modelsStale
                ? `Ollama not reachable at ${ollamaUrl} — showing cached inventory.`
                : `Ollama not reachable at ${ollamaUrl}.`}
              {ollamaError && ollamaError !== `Ollama not reachable at ${ollamaUrl}` && (
                <span className="text-amber-200/80"> {ollamaError}</span>
              )}
            </span>
          </p>
          <button
            onClick={refresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-500/30 text-amber-300 hover:bg-amber-500/20 transition-colors disabled:opacity-50 shrink-0"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} /> Retry
          </button>
        </div>
      )}

      {stateError && (
        <div className="flex items-center gap-2 p-3 rounded-xl border border-err/30 bg-err/5">
          <AlertTriangle size={14} className="text-err shrink-0" />
          <p className="text-xs text-err">{stateError}</p>
        </div>
      )}

      <section aria-label="Current selection" className="space-y-2">
        <SelectionRow
          label={PRIMARY_LABEL}
          desc={PRIMARY_DESC}
          model={primary}
          entry={modelByName(discovery, primary)}
          installedModels={chatModels}
          missing={primary !== '' && !discovery.installedNames.includes(primary)}
          caps={(discovery.capabilities as any ?? null)}
          recommended={singleRec?.model ?? ''}
          recommendation={singleRec}
          explanation={singleRec?.explanation ?? null}
          expanded={expandedWhy === 'primary'}
          onToggleWhy={() => setExpandedWhy(expandedWhy === 'primary' ? null : 'primary')}
          saving={saving}
          applying={applying}
          onSelect={onSelect}
          onUseRecommended={useRecommendedFor}
        />
      </section>
    

    

        <RecommendedSetup
          models={missingRecommended}
          installing={installing}
          onInstall={onInstall}
          onDismiss={onDismiss}
        />

        <section aria-label="Memory embedding model">
          <SectionHeader title="Memory model" subtitle="Used only to help Novi remember; it is not your chat model." />
          <div className="p-3 rounded-xl  border border-base-700 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="font-mono text-sm text-base-200 truncate">{embeddingName}</p>
              {!embeddingInstalled && <p className="text-[11px] text-amber-400 mt-1">Not installed — check General.</p>}
            </div>
            <StatusBadge status={embeddingInstalled ? 'installed' : 'missing'} />
          </div>
        </section>

        <section>
        <SectionHeader
          title="Model library"
          subtitle="Installed models and models available to install."
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
              {query
                ? `No models match "${query}".`
                : isUnreachable
                  ? 'Start Ollama or check Providers → Ollama URL'
                  : 'No models detected. Install one below.'}
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

// ── 0. Hardware ──────────────────────────────────────────────────────────

function HardwareBar({ hardware, provisional, onRefresh, refreshing }: {
  hardware: DiscoveryHardware
  provisional: boolean
  onRefresh: () => void
  refreshing: boolean
}) {
  const gpuName = hardware.gpu?.name ?? ''
  const vramGb = hardware.gpu?.vramTotalGb ?? null
  const ramGb = hardware.ramGb ?? 0
  const confToken = hardware.confidence ?? 'unknown'
  const confLabel = { high: 'High', medium: 'Medium', low: 'Low', unknown: 'Unknown' }[confToken] ?? confToken
  const confTone = confToken === 'high' ? 'text-emerald-400' : confToken === 'low' || confToken === 'unknown' ? 'text-amber-400' : 'text-base-300'
  return (
    <section aria-label="System hardware">
      <div className="flex items-center justify-between gap-3 p-3.5 rounded-xl bg-base-800/50 border border-base-700">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5">
          <HardwareFact icon={<Cpu size={12} />} label="GPU" value={gpuName || 'Unknown'} unknown={!gpuName} />
          <HardwareFact label="VRAM" value={vramGb == null ? 'Unknown' : `${vramGb} GB`} unknown={vramGb == null} />
          <HardwareFact icon={<MemoryStick size={12} />} label="System RAM" value={ramGb > 0 ? `${ramGb} GB` : 'Unknown'} unknown={ramGb <= 0} />
          <span className="flex items-center gap-1.5 text-[11px] text-base-400">
            <span className="text-base-500">Detection:</span>
            <span className={confTone}>{confLabel} confidence</span>
          </span>
        </div>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          title="Re-detect hardware and rescan models"
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium rounded-lg border border-base-700 text-base-400 hover:text-base-200 hover:border-base-600 transition-colors disabled:opacity-50 shrink-0"
        >
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          Rescan
        </button>
      </div>
      {provisional && (
        <div data-provisional className="flex items-center gap-1.5 text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2.5 py-1.5 rounded-lg mt-2">
          <AlertTriangle size={11} className="shrink-0" />
          <span>Some hardware details are unknown — recommendations below are provisional until detection improves.</span>
        </div>
      )}
    </section>
  )
}

function HardwareFact({ icon, label, value, unknown }: { icon?: React.ReactNode; label: string; value: string; unknown?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-base-400">
      {icon && <span className="text-base-500">{icon}</span>}
      <span className="text-base-500">{label}:</span>
      <span data-hardware-fact={label} className={unknown ? 'italic text-base-500' : 'text-base-200 font-medium'}>
        {value}
      </span>
    </span>
  )
}

// ── 2. Advisory recommendations ──────────────────────────────────────────

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
      
        <div className="flex items-center gap-2">
          <div>
            <p className="text-sm font-medium text-base-100 m-1">Recommended model unavailable</p>
            <p className="text-[11px] text-base-500 leading-relaxed m-1">
              Novi would recommend these for your hardware, but they are not installed.
              Installing happens only when you choose — skipping keeps your current eligible models.
            </p>
          </div>
        </div>
        <div className="space-y-2">
          {models.map((m) => {
            const busy = installing[m.name]
            return (
              <div key={m.name} className="p-3 rounded-lg bg-base-900/60 border border-base-700">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm text-base-100 font-mono truncate">{m.displayName}</p>
                      <StatusBadge status="available" />
                    </div>
                    <div className="flex flex-wrap items-center gap-1 mt-1">
                      <CapabilityChips caps={capsFromEntry(m) as any} />
                      {m.approxRamGb != null && (
                        <span className="text-[10px] text-base-500">~{m.approxRamGb} GB RAM footprint</span>
                      )}
                    </div>
                   
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
    </section>
  )
}

// ── 1. Current selection ─────────────────────────────────────────────────

function SelectionRow({ label, desc, model, entry, installedModels, missing, caps, recommended, recommendation, explanation, expanded, onToggleWhy, saving, applying, onSelect, onUseRecommended }: {
  label: string
  desc: string
  model: string
  entry?: DiscoveredModelEntry
  installedModels: DiscoveredModelEntry[]
  missing: boolean
  caps?: { vision?: boolean; tools?: boolean; reasoning?: boolean; thinking?: boolean; audio?: boolean; coding?: boolean } | null
  recommended: string
  recommendation: PrimaryRecommendation | null
  explanation: RecommendationExplanation | null
  expanded: boolean
  onToggleWhy: () => void
  saving: boolean
  applying: boolean
  onSelect: (model: string) => void
  onUseRecommended: () => void
}) {
  const usingRecommended = !!model && !!recommended && model === recommended
  const recommendationChanged = !!model && !!recommended && model !== recommended
  return (
    <>
      <div className="p-3.5 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm text-base-100 font-medium">{label}</p>
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
            {caps && <CapabilityChips caps={caps} />}
          </div>
          <p className="text-[11px] text-base-500 mt-0.5">{desc}</p>
          {recommended && (
            <p className="flex items-center gap-1 text-[11px] text-sky-400 mt-1">
              <Sparkles size={11} className="shrink-0" /> Recommended: <span className="font-mono">{recommended}</span>
            </p>
          )}
          {model && (
            <p className={`text-[10px] mt-1 font-mono truncate ${missing ? 'text-amber-400' : 'text-base-400'}`}>
              {missing
                ? `"${model}" is not installed — Novi cannot use it until it is.`
                : entry?.displayName ?? model}
            </p>
          )}
          {recommended && explanation && (
            <button
              onClick={onToggleWhy}
              title={`Why is ${recommended} recommended?`}
              className="text-[10px] text-base-500 hover:text-base-300 underline underline-offset-2 mt-1 transition-colors"
            >
              {expanded ? 'Why this model? ▴' : 'Why this model? ▾'}
            </button>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <label>
            <span className="sr-only">{label} model</span>
            <select
              disabled={saving}
              value={model}
              onChange={(e) => onSelect(e.target.value)}
              title={`Choose the model for ${label}`}
              aria-label={`${label} model`}
              className="bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40 disabled:opacity-50 disabled:cursor-not-allowed"
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
          {recommended && !usingRecommended && (
            <button
              onClick={onUseRecommended}
              disabled={applying || saving}
              title="Use the recommended model"
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg bg-accent/10 border border-accent/30 text-accent hover:bg-accent/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {applying ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
              {applying ? 'Applying…' : 'Use Recommended'}
            </button>
          )}
          {!recommended && (
            <p className="text-[10px] text-base-500">No recommendation available</p>
          )}
        </div>
      </div>
      {expanded && explanation && recommendation && (
        <RecommendationExplanation
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

function RecommendationExplanation({ recommendation, selected }: {
  recommendation: PrimaryRecommendation
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
      aria-label="Why this model"
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
        <p className="text-[10px] uppercase tracking-wide text-base-500">Evidence</p>
        <p className="flex items-center gap-1.5 text-base-300">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${capDot}`} />
          <span className={sourceTone}>{sourceLabel}</span>
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
            This will remove the model from your device. It will not change your model selection.
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
            {e.supported === false && <span className="text-red-400">Verified unsupported</span>}
            {(e.supported === null || e.supported === undefined) && (
              <span className="text-amber-400">Unknown — not yet verified (tap Rescan)</span>
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
