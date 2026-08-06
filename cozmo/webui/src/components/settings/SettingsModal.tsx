import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Search, Settings } from 'lucide-react'
import { fetchTools, fetchSkills } from '@/services/cozmo'
import { fetchConfig, saveConfig, fetchOllamaModels, fetchAvailableModels } from './api'
import { useToast } from '@/hooks/useToast'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { useProductConfig } from '@/hooks/useProductConfig'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { SECTIONS } from './constants'
import { ModelsSettings } from './ModelsSettings'
import { ToolsSettings } from './ToolsSettings'
import { MemorySettings } from './MemorySettings'
import { SkillsSection } from './SkillsSection'
import { ConnectorsSection } from './ConnectorsSection'
import { GeneralSettings } from './GeneralSettings'
import { AgentSettings } from './AgentSettings'
import type { SectionId, SettingsData, ToolInfo } from './types'
import type { Skill } from '@/types'

export type { SectionId }

interface Props {
  open: boolean
  onClose: () => void
  initialSection?: SectionId
  onCreateSkill?: () => void
}

export function SettingsModal({ open, onClose, initialSection, onCreateSkill }: Props) {
  const { showError } = useToast()
  const [section, setSection] = useState<SectionId>('general')
  const [search, setSearch] = useState('')
  const [config, setConfig] = useState<SettingsData | null>(null)
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [ollamaModels, setOllamaModels] = useState<string[]>([])
  const [availableModels, setAvailableModels] = useState<{ name: string; provider: string }[]>([])
  const [dirty, setDirty] = useState(false)
  const [skills, setSkills] = useState<Skill[]>([])
  const modalRef = useRef<HTMLDivElement>(null)

  useFocusTrap(modalRef, open)

  const discoveredModels = useMemo(
    () => [
      ...ollamaModels.map((name) => ({ name, provider: 'ollama' })),
      ...availableModels,
    ],
    [ollamaModels, availableModels]
  )
  const product = useProductConfig({ config, setConfig, setDirty, discoveredModels })

  useEffect(() => {
    if (!open) return
    if (initialSection) setSection(initialSection)
    fetchConfig().then((cfg) => {
      setConfig(cfg)
    }).catch(() => {
      showError("Couldn't load settings. Is Cozmo's backend running?")
    })
    fetchOllamaModels().then(setOllamaModels).catch(() => {})
    fetchAvailableModels().then(setAvailableModels).catch(() => {})
    fetchTools()
      .then(setTools)
      .catch(() => {})
    fetchSkills()
      .then(setSkills)
      .catch(() => {})
  }, [open, initialSection, showError])

  const updateToolPermission = (toolId: string, mode: string) => {
    if (!config) return
    setConfig({ ...config, permissions: { ...(config.permissions as Record<string, unknown> ?? {}), [toolId]: mode } } as SettingsData)
    setDirty(true)
  }

  const save = () => {
    if (!config) return
    const patch: Record<string, unknown> = { models: config.models }
    if ((config as any).llm) patch.llm = (config as any).llm
    if (config.permissions) patch.permissions = config.permissions
    patch.runtime = (config as any).runtime ?? { lightweight_mode: false }
    if ((config as any).agent) patch.agent = (config as any).agent
    if ((config as any).mcp) patch.mcp = (config as any).mcp
    if ((config as any).personality) patch.personality = (config as any).personality
    if ((config as any).memory) patch.memory = (config as any).memory
    if ((config as any).embedding) patch.embedding = (config as any).embedding
    saveConfig(patch).catch(() => {
      showError("Couldn't save settings — your changes weren't persisted.")
    })
    setDirty(false)
  }

  useEffect(() => {
    if (!open) return
    const handleClick = (e: MouseEvent) => {
      if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
        if (dirty) save()
        onClose()
      }
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (dirty) save()
        onClose()
      }
    }
    window.addEventListener('mousedown', handleClick)
    window.addEventListener('keydown', handleKey)
    return () => {
      window.removeEventListener('mousedown', handleClick)
      window.removeEventListener('keydown', handleKey)
    }
  }, [open, dirty, onClose])

  const filteredSections = useMemo(() => {
    if (!search) return SECTIONS
    const q = search.toLowerCase()
    return SECTIONS.filter((s) => s.label.toLowerCase().includes(q))
  }, [search])

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
            className="flex w-[800px] h-[560px] rounded-2xl border border-base-700 bg-base-900 shadow-panel overflow-hidden"
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
                    onClick={() => setSection(s.id)}
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
                <h2 className="text-sm font-semibold text-base-100">
                  {SECTIONS.find((s) => s.id === section)?.label}
                </h2>
                <div className="flex items-center gap-2">
                  {dirty && (
                    <button
                      onClick={save}
                      className="px-3 py-1 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors"
                    >
                      Save
                    </button>
                  )}
                  <button
                    onClick={() => {
                      if (dirty) save()
                      onClose()
                    }}
                    aria-label="Close settings"
                    className="p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
                  >
                    <X size={16} />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                {config === null && (
                  <LoadingSkeleton rows={5} compact />
                )}
                {config !== null && section === 'general' && (
                  <GeneralSettings
                    config={config}
                    setConfig={setConfig}
                    setDirty={setDirty}
                    profiles={product.profiles}
                    activeProfileId={product.activeProfileId}
                    profileSummaries={product.profileSummaries}
                    onApplyProfile={product.applyProfile}
                  />
                )}
                {section === 'models' && (
                  <ModelsSettings
                    config={config}
                    catalog={product.catalog}
                    availableModels={availableModels}
                    setConfig={setConfig}
                    setDirty={setDirty}
                  />
                )}
                {section === 'memory' && <MemorySettings config={config} setConfig={setConfig} setDirty={setDirty} />}
                {section === 'tools' && (
                  <ToolsSettings
                    tools={tools}
                    config={config}
                    updateToolPermission={updateToolPermission}
                  />
                )}
                {section === 'mcp' && <ConnectorsSection config={config} setConfig={setConfig} setDirty={setDirty} />}
                {section === 'skills' && (
                  <SkillsSection
                    skills={skills}
                    onRefresh={() => fetchSkills().then(setSkills).catch(() => {})}
                    onCreateSkill={onCreateSkill}
                    onClose={onClose}
                  />
                )}
                {section === 'advanced' && (
                  <AgentSettings config={config} setConfig={setConfig} setDirty={setDirty} />
                )}
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
