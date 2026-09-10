import { useEffect, useMemo, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Search, Settings, Server, SlidersHorizontal, Loader2 } from 'lucide-react'
import { fetchTools, fetchSkills } from '@/services/novi'
import type { SchemaResponse } from './api'
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
import { PermissionsSettings } from './PermissionsSettings'
import type { SectionId, ToolInfo } from './types'
import type { Skill } from '@/types'

export type { SectionId }

interface Props {
  open: boolean
  onClose: () => void
  initialSection?: SectionId
  onCreateSkill?: () => void
  onSectionChange?: (section: SectionId) => void
}

const PAGE_LABEL: Record<string, string> = {
  general: 'General',
  models: 'Models',
  memory: 'Memory',
  skills: 'Skills',
  connectors: 'Connectors',
  permissions: 'Permissions',
  developer: 'Developer',
}

const NAV_GROUP: Record<string, string> = {
  general: 'Get started',
  models: 'Get started',
  memory: 'Get started',
  skills: 'Customize',
  connectors: 'Control',
  permissions: 'Control',
  developer: 'Advanced',
}

// Beta IA hides Developer from the sidebar nav. Advanced escape hatch:
// typing `developer` (optionally `>developer`) in settings search, or
// localStorage `novi_dev=1`, reveals the hidden Developer entry.
const DEV_FLAG = 'novi_dev'

function isDevUnlocked(): boolean {
  try {
    return localStorage.getItem(DEV_FLAG) === '1'
  } catch {
    return false
  }
}

export function SettingsModal({ open, onClose, initialSection, onCreateSkill, onSectionChange }: Props) {
  const [section, setSection] = useState<SectionId>('general')
  const framework = useFrameworkSettings()
  const [search, setSearch] = useState('')
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const modalRef = useRef<HTMLDivElement>(null)

  useFocusTrap(modalRef, open)

  const schema = framework.schema

  const migrateSection = (target: SectionId) => {
    setSection(target)
    onSectionChange?.(target)
  }

  const filteredSections = useMemo(() => {
    const pages = SECTIONS.map((s) => ({ id: s.id, label: s.label, icon: s.icon }))
    const q = search.trim().toLowerCase().replace(/^>/, '')
    const devMatch = q !== '' && 'developer'.includes(q)
    const devUnlocked = isDevUnlocked()
    const all =
      devUnlocked || devMatch
        ? [...pages, { id: 'developer' as SectionId, label: 'Developer', icon: Server }]
        : pages
    if (!q) return all
    return all.filter((s) => s.label.toLowerCase().includes(q))
  }, [search])

  const reloadData = () => {
    if (!open) return
    if (initialSection) setSection(initialSection)
    void fetchTools().then(setTools).catch(() => {})
    void fetchSkills().then(setSkills).catch(() => {})
  }

  const refreshSkills = () => {
    void fetchSkills().then(setSkills).catch(() => {})
  }

  useEffect(() => { reloadData() }, [open, initialSection]) // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
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
                  {framework.loading && <Loader2 size={12} className="animate-spin text-accent ml-auto" aria-label="Loading settings" />}
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
              <div className="flex-1 overflow-y-auto py-2">
                {filteredSections.map((s, index) => (
                  <div key={s.id}>
                  {(index === 0 || NAV_GROUP[s.id] !== NAV_GROUP[filteredSections[index - 1].id]) && (
                    <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-base-600">{NAV_GROUP[s.id]}</p>
                  )}
                  <button
                    key={s.id}
                    onClick={() => migrateSection(s.id as SectionId)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors ${
                      section === s.id
                        ? 'bg-base-800 text-base-100 border-l-2 border-accent'
                        : 'text-base-400 hover:text-base-200 hover:bg-base-850'
                    }`}
                  >
                    <s.icon size={14} />
                    {s.label}
                  </button>
                  </div>
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
                    onRefresh={framework.refreshDiscovery}
                    loading={false}
                  />
                )}

                {!framework.loading && section === 'models' && (
                  <ModelsSettings
                    discovery={framework.discovery}
                    schema={framework.schema}
                    embeddingModel={(framework.values['embedding.model'] as string) ?? ''}
                    installing={framework.installs}
                    onInstall={framework.install}
                    onDelete={framework.removeModel}
                    onDismiss={framework.dismissRecommended}
                    onRefresh={framework.refreshDiscovery}
                    loading={false}
                    onSaveSelection={framework.savePrimaryModel}
                    onApplyRecommended={framework.applyRecommended}
                  />
                )}

                {!framework.loading && section === 'memory' && (
                  <MemorySettings framework={framework} />
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
                  <ConnectorsSection framework={framework} />
                )}

                {!framework.loading && section === 'permissions' && (
                  <PermissionsSettings tools={tools} framework={framework} />
                )}

                {!framework.loading && section === 'developer' && (
                  <DeveloperPage schema={schema} framework={framework} />
                )}
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function DeveloperPage({ schema, framework }: {
  schema: SchemaResponse | null
  framework: ReturnType<typeof useFrameworkSettings>
}) {
  const developer = schema?.settings.filter((s) => s.category === 'developer') ?? []
  // Expert-owned memory fields only — embedding.model (category models) already
  // lives on the Models page (Task 4/5) and must not be duplicated here.
  const expertMemory = schema?.settings.filter((s) => s.owner === 'memory' && s.category === 'developer') ?? []
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
        {expertMemory.map((s) => (
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
