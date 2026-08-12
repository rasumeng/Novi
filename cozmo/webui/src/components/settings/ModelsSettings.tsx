import { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Loader2,
  RefreshCw,
  Sparkles,
  Wand2,
  ShieldQuestion,
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
  mode?: string
  source?: string
  customAssign?: Record<string, string>
  onSetModelsState?: (mode: 'automatic' | 'custom', assign?: Record<string, string>) => Promise<{ ok: boolean; error?: string }>
}

/** The four user-facing capabilities. Embeddings & internal roles are not shown. */
const CAPABILITIES: { key: string; capability: string; role: string; desc: string }[] = [
  { key: 'chat', capability: 'Chat', role: 'chat', desc: 'Everyday conversation and general interaction.' },
  { key: 'reasoning', capability: 'Reasoning', role: 'planner', desc: 'Planning, research, and deeper cognitive work.' },
  { key: 'coding', capability: 'Coding', role: 'coder', desc: 'Code generation, editing, and debugging.' },
  { key: 'vision', capability: 'Vision', role: 'vision', desc: 'Image understanding and visual analysis.' },
]

interface ModelsState {
  ok: boolean
  error?: string
}

const NOOP = async (): Promise<ModelsState> => ({ ok: true })

/**
 * Models — the user-facing configuration surface for the four capabilities
 * Cozmo cares about (Chat / Reasoning / Coding / Vision) plus the model library.
 *
 * M3.2: the Automatic <-> Custom state machine. Automatic resolves through the
 * backend ResolutionLayer; Custom persists explicit capability intent under
 * ``models.custom.assign.*`` and resolves it to ``llm.roles.*``. All writes go
 * through the Configuration Framework — no localStorage, no direct TOML.
 *
 * Embeddings and internal runtime roles (classifier/router/orchestrator/planner)
 * are deliberately NOT exposed here — those live in Developer.
 */
export function ModelsSettings({ discovery, installing, onInstall, onDismiss, onRefresh, loading, mode, source, customAssign, onSetModelsState }: Props) {
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [stateError, setStateError] = useState<string | null>(null)

  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const refresh = async () => {
    setRefreshing(true)
    await onRefresh()
    setRefreshing(false)
  }

  const isCustom = (mode ?? 'automatic') === 'custom'
  const isAutomatic = !isCustom
  const assign = customAssign ?? {}
  const transition = onSetModelsState ?? NOOP

  const modelByName = (name: string): DiscoveredModelEntry | undefined =>
    name ? discovery.models.find((m) => m.name === name) : undefined

  const isInstalled = (name: string): boolean =>
    name ? discovery.installedNames.includes(name) : false

  const installedModels = discovery.models.filter((m) => m.status === 'installed')

  const effectiveModel = (cap: { role: string }) => discovery.roles?.[cap.role] ?? ''
  const intentModel = (cap: { key: string }) => assign[cap.key] ?? ''

  const runTransition = async (nextMode: 'automatic' | 'custom', patch?: Record<string, string>) => {
    setSaving(true)
    setStateError(null)
    const res = await transition(nextMode, patch)
    if (!res.ok) setStateError(res.error ?? "Couldn't update model configuration.")
    setSaving(false)
  }

  const enterCustom = () => {
    // Seed Custom from the currently effective Automatic assignments so the
    // user never sees blank selectors. Embeddings are never seeded.
    const seed: Record<string, string> = {}
    for (const c of CAPABILITIES) seed[c.key] = effectiveModel(c)
    void runTransition('custom', seed)
  }

  const backToAutomatic = () => {
    void runTransition('automatic')
  }

  const onCapabilityChange = (cap: string, model: string) => {
    void runTransition('custom', { [cap]: model })
  }

  const rows = discovery.models
    .filter((m) => m.name.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => {
      const rank = { installed: 0, missing: 1, available: 2 } as Record<string, number>
      return (rank[a.status] ?? 3) - (rank[b.status] ?? 3)
    })

  const missingCount = discovery.missingModels.length
  const installedCount = discovery.models.filter((m) => m.status === 'installed').length

  // M3.4: catalog models Cozmo recommends for Automatic that are NOT installed
  // and the user has not explicitly declined. Embeddings are never surfaced
  // here (backend excludes them; capability filter below is defense-in-depth).
  const userCapabilityKeys = CAPABILITIES.map((c) => c.key)
  const missingRecommended = discovery.models.filter(
    (m) =>
      m.status === 'available' &&
      !discovery.dismissedRecommended?.includes(m.name) &&
      Object.keys(m.capabilities ?? {}).some((c) => userCapabilityKeys.includes(c)),
  )

  return (
    <div className="space-y-6">
      {/* A. Configuration Mode */}
      <ConfigMode
        isCustom={isCustom}
        isAutomatic={isAutomatic}
        saving={saving}
        onEnterCustom={enterCustom}
        onBackToAutomatic={backToAutomatic}
      />

      {stateError && (
        <div className="flex items-center gap-2 p-3 rounded-xl border border-err/30 bg-err/5">
          <AlertTriangle size={14} className="text-err shrink-0" />
          <p className="text-xs text-err">{stateError}</p>
        </div>
      )}

      {/* M3.4 — explicit-consent setup for missing recommended models (Automatic only) */}
      {isAutomatic && (
        <RecommendedSetup
          models={missingRecommended}
          installing={installing}
          onInstall={onInstall}
          onDismiss={onDismiss}
        />
      )}

      {/* 3. Current Assignments */}
      <section>
        <SectionHeader
          title="Current assignments"
          subtitle="The models Cozmo is using right now for each capability."
        />
        <div className="space-y-2">
          {CAPABILITIES.map((c) => {
            const model = effectiveModel(c)
            const entry = modelByName(model)
            const intent = intentModel(c)
            const missing = isCustom && intent !== '' && !isInstalled(intent)
            return (
              <AssignmentRow
                key={c.role}
                capability={c.capability}
                desc={c.desc}
                model={model}
                entry={entry}
                derived={isAutomatic || intent === ''}
                missing={missing}
                intentModel={intent}
              />
            )
          })}
        </div>
      </section>

      {/* 5. Automatic explanation */}
      {isAutomatic && <AutomaticExplanation discovery={discovery} modelByName={modelByName} />}

      {/* 4. Custom configuration */}
      <section>
        <SectionHeader
          title="Custom configuration"
          subtitle="Choose which installed model handles each capability."
        />
        <div className="p-3 rounded-xl bg-base-800/40 border border-base-700 mb-2">
          <p className="text-[11px] text-base-500">
            {isAutomatic
              ? 'Cozmo is currently choosing automatically. Switch to Custom to select models yourself.'
              : 'Pick a model for each capability from what is installed on this machine. Unassigned capabilities inherit Automatic.'}
          </p>
        </div>
        <div className="space-y-2">
          {CAPABILITIES.map((c) => {
            const intent = intentModel(c)
            const missing = intent !== '' && !isInstalled(intent)
            return (
              <CustomCapabilityRow
                key={c.role}
                capability={c.capability}
                capKey={c.key}
                current={intent}
                effective={effectiveModel(c)}
                installedModels={installedModels}
                editable={isCustom}
                saving={saving}
                missing={missing}
                onSelect={onCapabilityChange}
              />
            )
          })}
        </div>
        <p className="text-[11px] text-base-500 mt-2">
          Changes save automatically. Embeddings and internal routing stay managed by Cozmo.
        </p>
      </section>

      {/* 6. Model Library */}
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

// ── A. Configuration Mode ────────────────────────────────────────────────

function ConfigMode({ isCustom, isAutomatic, saving, onEnterCustom, onBackToAutomatic }: {
  isCustom: boolean
  isAutomatic: boolean
  saving: boolean
  onEnterCustom: () => void
  onBackToAutomatic: () => void
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <ModeCard
        active={isAutomatic}
        icon={<Wand2 size={15} />}
        title="Automatic"
        desc={isAutomatic ? 'Active — Cozmo chooses the best models for your hardware and installed models.' : 'Let Cozmo choose the best models for you.'}
        accent
      >
        {!isAutomatic && (
          <button
            onClick={onBackToAutomatic}
            disabled={saving}
            className="mt-2 w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-sky-500/15 text-sky-300 border border-sky-500/20 hover:bg-sky-500/25 transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
            Use Automatic
          </button>
        )}
      </ModeCard>
      <ModeCard
        active={isCustom && !isAutomatic}
        icon={<ShieldQuestion size={15} />}
        title="Custom"
        desc={isAutomatic ? 'Choose which models Cozmo uses for specific capabilities.' : 'Active — you choose which models handle each capability.'}
      >
        {!isCustom && (
          <button
            onClick={onEnterCustom}
            disabled={saving}
            className="mt-2 w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/15 text-amber-300 border border-amber-500/20 hover:bg-amber-500/25 transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <ShieldQuestion size={13} />}
            Switch to Custom
          </button>
        )}
      </ModeCard>
    </div>
  )
}

function ModeCard({ active, icon, title, desc, accent, children }: {
  active: boolean
  icon: React.ReactNode
  title: string
  desc: string
  accent?: boolean
  children?: React.ReactNode
}) {
  return (
    <div
      className={`p-3 rounded-xl border transition-colors ${
        active
          ? accent
            ? 'border-sky-500/40 bg-sky-500/5'
            : 'border-amber-500/40 bg-amber-500/5'
          : 'border-base-700 bg-base-800/40'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className={active ? 'text-sky-400' : 'text-base-400'}>{icon}</span>
        <p className="text-sm font-medium text-base-100">{title}</p>
        {active && (
          <span className={`ml-auto text-[10px] font-medium px-2 py-0.5 rounded-full border ${
            accent ? 'text-sky-400 bg-sky-500/10 border-sky-500/20' : 'text-amber-400 bg-amber-500/10 border-amber-500/20'
          }`}>
            Active
          </span>
        )}
      </div>
      <p className="text-[11px] text-base-500 leading-relaxed">{desc}</p>
      {children}
    </div>
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
              Cozmo would prefer these for Automatic mode on your hardware, but they are not installed.
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

// ── 3. Current Assignments ───────────────────────────────────────────────

function AssignmentRow({ capability, desc, model, entry, derived, missing, intentModel }: {
  capability: string
  desc: string
  model: string
  entry?: DiscoveredModelEntry
  derived: boolean
  missing: boolean
  intentModel: string
}) {
  const installed = entry?.status === 'installed'
  return (
    <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm text-base-100 font-medium">{capability}</p>
          <span className="text-[10px] text-base-400 bg-base-800 border border-base-700 px-1.5 py-0.5 rounded">
            {derived ? 'Automatic' : 'Custom'}
          </span>
        </div>
        <p className="text-[11px] text-base-500 mt-0.5">{desc}</p>
      </div>
      <div className="text-right shrink-0 min-w-0">
        {model ? (
          <>
            <p className="text-sm text-base-100 font-mono truncate">{entry?.displayName ?? model}</p>
            <p className={`text-[10px] mt-0.5 ${missing ? 'text-amber-400' : 'text-base-500'}`}>
              {missing
                ? `your choice (${intentModel}) is unavailable — using fallback`
                : installed ? 'installed' : entry ? entry.status : 'not assigned'}
            </p>
          </>
        ) : (
          <p className="text-xs text-amber-400">No model assigned</p>
        )}
      </div>
    </div>
  )
}

// ── 5. Automatic explanation ─────────────────────────────────────────────

function AutomaticExplanation({ discovery, modelByName }: {
  discovery: DiscoveryPayload
  modelByName: (name: string) => DiscoveredModelEntry | undefined
}) {
  const confidence = discovery.models.find((m) => m.eligibility?.hardwareConfidence)?.eligibility?.hardwareConfidence ?? 'unknown'
  const provisional = confidence === 'low' || confidence === 'unknown'
  const ram = discovery.hardware.ramGb

  return (
    <section>
      <SectionHeader
        title="How models were chosen"
        subtitle="An Automatic resolution, computed by Cozmo from your hardware and installed models. Cozmo re-checks this automatically."
      />
      <div className="p-4 rounded-xl bg-base-800/40 border border-base-700 space-y-3">
        <div className="flex items-center gap-2">
          <Cpu size={14} className="text-accent" />
          <p className="text-sm font-medium text-base-100">Hardware detected</p>
        </div>
        <p className="text-xs text-base-500">
          {ram > 0 ? `${ram} GB RAM detected` : 'Hardware unknown — Cozmo is being conservative.'}
          {confidence !== 'unknown' && ` Hardware confidence: ${confidence}.`}
        </p>

        {provisional && (
          <div className="flex items-start gap-2 p-3 rounded-lg border border-amber-500/30 bg-amber-500/5">
            <AlertTriangle size={14} className="text-amber-400 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-300">
              This selection is provisional because Cozmo has limited confidence in the hardware it detected.
            </p>
          </div>
        )}

        {discovery.models.filter((m) => m.recommended && m.status === 'installed').length === 0 &&
          discovery.installedNames.length > 0 && (
            <p className="text-xs text-base-500">
              None of the installed models are marked as trusted by Cozmo, so the selection may be less than ideal.
            </p>
          )}

        {CAPABILITIES.map((c) => {
          const model = discovery.roles?.[c.role] ?? ''
          if (!model) return
          const entry = modelByName(model)
          return (
            <div key={c.role} className="flex items-start justify-between gap-3">
              <p className="text-xs text-base-400 min-w-[70px]">{c.capability}</p>
              <div className="text-right text-[11px] text-base-500 flex-1 min-w-0">
                {entry?.reasons.length ? (
                  <p className="text-accent truncate">
                    <Sparkles size={10} className="inline mr-1" />
                    {entry.reasons.join(' · ')}
                  </p>
                ) : (
                  <p>selected by Cozmo</p>
                )}
                {entry?.qualification && (
                  <p className="text-base-500 capitalize">Qualification: {entry.qualification}</p>
                )}
                {entry?.caveats?.length ? (
                  <p className="text-amber-400">{entry.caveats.join(' · ')}</p>
                ) : null}
              </div>
            </div>
          )
        })}
        <p className="text-[10px] text-base-600">
          Derived at run time — not saved to configuration. Cozmo remains the source of truth.
        </p>
      </div>
    </section>
  )
}

// ── 4. Custom configuration ──────────────────────────────────────────────

function CustomCapabilityRow({ capability, capKey, current, effective, installedModels, editable, saving, missing, onSelect }: {
  capability: string
  capKey: string
  current: string
  effective: string
  installedModels: DiscoveredModelEntry[]
  editable: boolean
  saving: boolean
  missing: boolean
  onSelect: (capability: string, model: string) => void
}) {
  const options = installedModels.length > 0 ? installedModels : []
  return (
    <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-base-100 font-medium">{capability}</p>
        <p className={`text-[11px] mt-0.5 ${missing ? 'text-amber-400' : 'text-base-500'}`}>
          {current
            ? missing
              ? `Your choice "${current}" is not installed — Cozmo is temporarily using a fallback.`
              : 'Selected by you'
            : `Inherits Automatic (${effective || 'none installed'})`}
        </p>
      </div>
      <label className="shrink-0">
        <span className="sr-only">{capability} model</span>
        <select
          disabled={!editable || saving}
          value={current || ''}
          onChange={(e) => onSelect(capKey, e.target.value)}
          title={editable ? `Choose the model for ${capability}` : 'Switch to Custom to choose a model'}
          data-capability={capability}
          className="bg-base-900 border border-base-700 rounded-lg px-2 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <option value="">Inherit Automatic ({effective || 'none installed'})</option>
          {options.map((m) => (
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