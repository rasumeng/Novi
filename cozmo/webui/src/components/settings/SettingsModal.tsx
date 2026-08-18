import { useEffect, useMemo, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Search, Settings, SlidersHorizontal } from 'lucide-react'
import { fetchTools, fetchSkills } from '@/services/cozmo'
import { fetchConfig, saveConfig, type SchemaResponse } from './api'
import { useToast } from '@/hooks/useToast'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { useFrameworkSettings } from '@/hooks/useFrameworkSettings'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { SECTIONS } from './constants'
import { GeneralSettings } from './GeneralSettings'
import { ModelsSettings } from './ModelsSettings'
import { SettingField } from './SettingField'
import { MemorySettings } from './MemorySettings'
import { SkillsSection } from './SkillsSection'
import { ConnectorsSection } from './ConnectorsSection'
import { AgentSettings } from './AgentSettings'
import { PermissionsSettings } from './PermissionsSettings'
import type { SectionId, SettingsData, ToolInfo } from './types'
import type { Skill } from '@/types'

export type { SectionId }

interface Props {
  open: boolean
  onClose: () => void
  initialSection?: SectionId
  onCreateSkill?: () => void
}

const PAGE_LABEL: Record<string, string> = {
  general: 'General',
  models: 'Models',
  agent: 'Agent',
  memory: 'Memory',
  skills: 'Skills',
  connectors: 'Connectors',
  permissions: 'Permissions',
  developer: 'Developer',
}

export function SettingsModal({ open, onClose, initialSection, onCreateSkill }: Props) {
  const { showError } = useToast()
  const framework = useFrameworkSettings()
  const [section, setSection] = useState<SectionId>('general')
  const [search, setSearch] = useState('')
  const [legacyConfig, setLegacyConfig] = useState<SettingsData | null>(null)
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const modalRef = useRef<HTMLDivElement>(null)
  const initialLegacyRef = useRef<SettingsData | null>(null)

  useFocusTrap(modalRef, open)

  // The framework schema is the single source of truth for every page.
  const schema = framework.schema

  const updateLegacy = (next: SettingsData) => {
    setLegacyConfig(next)
    flushLegacy(next)
  }

  // Legacy nested config (memory/tools/agent/mcp) persists live through the
  // framework endpoint; the legacy PUT is kept only as a compat fallback.
  const flushLegacy = (next: SettingsData) => {
    if (!legacyConfig) return
    const prev = legacyConfig
    const ids = new Set(schema?.settings.map((s) => s.id) ?? [])
    for (const path of collectLeafPaths(prev, next)) {
      if (ids.has(path)) {
        const val = readLeaf(next, path)
        void framework.set(path, val)
      }
    }
    const patch = legacyPatch(next, initialLegacyRef.current)
    if (Object.keys(patch).length > 0) {
      void saveConfig(patch).catch(() => showError("Some advanced settings didn't persist."))
    }
  }

  const updateToolPermission = (toolId: string, mode: string) => {
    if (!legacyConfig) return
    const next = {
      ...legacyConfig,
      permissions: { ...((legacyConfig.permissions as Record<string, unknown>) ?? {}), [toolId]: mode },
    } as SettingsData
    updateLegacy(next)
  }

  const migrateSection = (target: SectionId) => setSection(target)

  const filteredSections = useMemo(() => {
    const pages = SECTIONS.map((s) => ({ id: s.id, label: s.label, icon: s.icon }))
    if (!search) return pages
    const q = search.toLowerCase()
    return pages.filter((s) => s.label.toLowerCase().includes(q))
  }, [search])

  const reloadData = () => {
    if (!open) return
    if (initialSection) setSection(initialSection)
    void fetchConfig().then((cfg) => {
      setLegacyConfig(cfg)
      initialLegacyRef.current = JSON.parse(JSON.stringify(cfg))
    }).catch(() => {})
    void fetchTools().then(setTools).catch(() => {})
    void fetchSkills().then(setSkills).catch(() => {})
  }

  const refreshSkills = () => {
    void fetchSkills().then(setSkills).catch(() => {})
  }

  useEffect(() => { reloadData() }, [open, initialSection]) // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    if (legacyConfig) flushLegacy(legacyConfig)
    onClose()
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
        >
          <motion.div
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.15, ease: 'easeOut' }}
            className="flex w-[75vw] h-[75vh] min-w-[700px] min-h-[500px] max-w-[1800px] max-h-[1600px] rounded-2xl border border-base-700 bg-base-900 shadow-panel overflow-hidden"
          >
            <div className="w-48 shrink-0 border-r border-base-800 flex flex-col bg-base-950/50">
              <div className="p-3 border-b border-base-800">
                <div className="flex items-center gap-2 mb-3">
                  <Settings size={16} className="text-accent" />
                  <span className="text-sm font-semibold text-base-100">Settings</span>
                </div>
                <div className="relative">
                  <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-500" />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search settings..."
                    aria-label="Search settings"
                    className="w-full bg-base-800 border border-base-700 rounded-lg pl-7 pr-2.5 py-1.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 transition-colors"
                  />
                </div>
              </div>
              <div className="flex-1 overflow-y-auto py-1">
                {filteredSections.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setSection(s.id as SectionId)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors ${
                      section === s.id
                        ? 'bg-base-800 text-base-100 border-l-2 border-accent'
                        : 'text-base-400 hover:text-base-200 hover:bg-base-850'
                    }`}
                  >
                    <s.icon size={14} />
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex-1 flex flex-col min-w-0">
              <div className="flex items-center justify-between px-5 h-12 border-b border-base-800 shrink-0">
                <h2 className="text-sm font-semibold text-base-100">{PAGE_LABEL[section]}</h2>
                <div className="flex items-center gap-2">
                  <span className="hidden text-[11px] text-base-500 sm:block">Changes save automatically</span>
                  <button
                    onClick={close}
                    aria-label="Close settings"
                    className="p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
                  >
                    <X size={16} />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                {framework.loading && <LoadingSkeleton rows={5} compact />}

                {!framework.loading && section === 'general' && (
                  <GeneralSettings
                    discovery={framework.discovery}
                    schema={framework.schema}
                    installing={framework.installs}
                    onInstall={framework.install}
                    onNavigate={migrateSection}
                    loading={false}
                  />
                )}

                {!framework.loading && section === 'models' && (
                  <ModelsSettings
                    discovery={framework.discovery}
                    schema={framework.schema}
                    installing={framework.installs}
                    onInstall={framework.install}
                    onDelete={framework.removeModel}
                    onDismiss={framework.dismissRecommended}
                    onRefresh={framework.refreshDiscovery}
                    loading={false}
                    onSaveSelection={framework.saveWorkloadSelection}
                    onApplyRecommended={framework.applyRecommended}
                  />
                )}

                {!framework.loading && section === 'agent' && (
                  <AgentSettings config={legacyConfig} setConfig={updateLegacy} setDirty={() => {}} />
                )}

                {!framework.loading && section === 'memory' && (
                  <MemorySettings config={legacyConfig} setConfig={updateLegacy} setDirty={() => {}} />
                )}

                {!framework.loading && section === 'skills' && (
                  <SkillsSection
                    skills={skills}
                    onRefresh={refreshSkills}
                    onCreateSkill={onCreateSkill}
                    onClose={close}
                  />
                )}

                {!framework.loading && section === 'connectors' && (
                  <ConnectorsSection config={legacyConfig} setConfig={updateLegacy} setDirty={() => {}} />
                )}

                {!framework.loading && section === 'permissions' && (
                  <PermissionsSettings tools={tools} config={legacyConfig} updateToolPermission={updateToolPermission} />
                )}

                {!framework.loading && section === 'developer' && (
                  <DeveloperPage schema={schema} framework={framework} config={legacyConfig} updateConfig={updateLegacy} />
                )}
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function DeveloperPage({ schema, framework, config, updateConfig }: {
  schema: SchemaResponse | null
  framework: ReturnType<typeof useFrameworkSettings>
  config: SettingsData | null
  updateConfig: (next: SettingsData) => void
}) {
  const developer = schema?.settings.filter((s) => s.category === 'developer') ?? []
  const embedding = schema?.settings.filter((s) => s.owner === 'memory') ?? []
  const providers = schema?.settings.filter((s) => s.owner === 'providers') ?? []
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl bg-accent/15 text-accent flex items-center justify-center shrink-0">
          <SlidersHorizontal size={17} />
        </div>
        <div>
          <p className="text-sm text-base-100 font-medium">Expert configuration</p>
          <p className="text-xs text-base-500 mt-0.5">
            Internal, runtime-level controls and diagnostics. Most people won't need to touch these.
          </p>
        </div>
      </div>

      <section className="space-y-2 pt-2">
        <h3 className="text-xs uppercase tracking-wide text-base-500 font-semibold">Providers</h3>
        {providers.map((s) => (
          <SettingField
            key={s.id}
            setting={s}
            value={framework.values[s.id]}
            onChange={(id, v) => void framework.set(id, v)}
          />
        ))}
      </section>

      <section className="space-y-2 pt-2">
        <h3 className="text-xs uppercase tracking-wide text-base-500 font-semibold">Embeddings</h3>
        {embedding.map((s) => (
          <SettingField
            key={s.id}
            setting={s}
            value={framework.values[s.id]}
            onChange={(id, v) => void framework.set(id, v)}
          />
        ))}
      </section>

      <section className="space-y-2 pt-2">
        <h3 className="text-xs uppercase tracking-wide text-base-500 font-semibold">Other expert settings</h3>
        {developer.map((s) => (
          <SettingField
            key={s.id}
            setting={s}
            value={framework.values[s.id]}
            onChange={(id, v) => void framework.set(id, v)}
          />
        ))}
      </section>
    </div>
  )
}

// ── nested-config leaf diffing ────────────────────────────────────────────

function collectLeafPaths(prev: Record<string, unknown>, next: Record<string, unknown>): string[] {
  const out: string[] = []
  const walk = (a: Record<string, unknown>, b: Record<string, unknown>, prefix: string) => {
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const path = prefix ? `${prefix}.${key}` : key
      const av = a[key]
      const bv = b[key]
      if (typeof av === 'object' && av !== null && typeof bv === 'object' && bv !== null) {
        walk(av as Record<string, unknown>, bv as Record<string, unknown>, path)
      } else if (av !== bv) {
        out.push(path)
      }
    }
  }
  walk(prev, next, '')
  return out
}

function readLeaf(obj: Record<string, unknown>, path: string): unknown {
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

export function legacyPatch(next: SettingsData, initial: SettingsData | null): Record<string, unknown> {
  // Framework-owned roots are never bulk-written: workload model selection and
  // the llm namespace persist through the framework (selection endpoint /
  // schema settings), not through the legacy PUT. Only roots a legacy page may
  // have actually changed are PUT, and only when they differ from the snapshot
  // taken when the modal opened — a stale root must never clobber a change made
  // elsewhere in the same session (this was reverting the workload selection
  // back to the pre-open value on modal close).
  const keys = ['permissions', 'runtime', 'agent', 'mcp', 'personality', 'memory', 'embedding'] as const
  const patch: Record<string, unknown> = {}
  for (const k of keys) {
    const nv = (next as unknown as Record<string, unknown>)[k]
    if (nv === undefined || nv === null) continue
    if (typeof nv === 'object' && Object.keys(nv).length === 0) continue
    if (initial) {
      const iv = (initial as unknown as Record<string, unknown>)[k]
      if (JSON.stringify(iv) === JSON.stringify(nv)) continue
    }
    patch[k] = nv
  }
  return patch
}