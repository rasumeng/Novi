import { useState } from 'react'
import { ChevronDown, TriangleAlert } from 'lucide-react'
import { BUILTIN_ROLES, PRESET_META } from './constants'
import type { SettingsData } from './types'
import { roleModelPatch } from '@/product/configLayer'

interface Props {
  config: SettingsData
  modelOptions: string[]
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
}

// The backend's actual routing architecture (classifier/router/orchestrator/
// etc.) — unchanged from before this milestone, just relocated behind an
// explicit expansion instead of being the default view. Nothing here was
// rewritten; this is the same per-role override logic Cozmo has always had.
export function ExpertModelConfig({ config, modelOptions, setConfig, setDirty }: Props) {
  const [expanded, setExpanded] = useState(false)

  const llm = config.llm || {}
  const defaultModel = llm.default_model || ''
  const llmRoles: Record<string, any> = llm.roles || {}
  const modelSet = new Set(modelOptions)

  const setDefaultModel = (model: string) => {
    setConfig({ ...config, llm: { ...llm, default_model: model } })
    setDirty(true)
  }

  const setRoleModel = (role: string, model: string) => {
    setConfig({ ...config, ...roleModelPatch(config, role, model) })
    setDirty(true)
  }

  return (
    <div className="rounded-xl border border-base-700 overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-base-800/50 transition-colors"
      >
        <TriangleAlert size={13} className="text-base-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm text-base-200 font-medium">Expert Configuration</p>
          <p className="text-[11px] text-base-500">Direct control over Cozmo's internal model routing. Most people don't need this.</p>
        </div>
        <ChevronDown size={14} className={`text-base-500 transition-transform shrink-0 ${expanded ? 'rotate-180' : ''}`} />
      </button>

      {expanded && (
        <div className="px-3 pb-4 pt-1 space-y-5 border-t border-base-800 bg-base-900/30">
          <div>
            <p className="text-sm text-base-100 font-medium mb-1">Default Model</p>
            <p className="text-xs text-base-500 mb-2">Used for anything not explicitly assigned below.</p>
            <select
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              className="w-full bg-base-900 border border-base-700 rounded-lg px-3 py-2 text-sm text-base-200 outline-none focus:border-accent/40"
            >
              <option value="">Select a model...</option>
              {modelOptions.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
              {defaultModel && !modelSet.has(defaultModel) && (
                <option value={defaultModel}>{defaultModel} (not found)</option>
              )}
            </select>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <p className="text-sm text-base-100 font-medium">Routing Roles</p>
              <span className="text-xs text-base-500">Optional — leave as "Use default" to inherit</span>
            </div>
            <p className="text-xs text-base-500 mb-2">
              Cozmo splits work across internal roles. Pin specific models to individual roles when needed.
            </p>
            <div className="space-y-1.5">
              {BUILTIN_ROLES.map((role) => {
                const roleSpec = llmRoles[role]
                const currentModel = roleSpec?.model || roleSpec || ''
                return (
                  <div key={role} className="flex items-center justify-between p-2.5 rounded-xl bg-base-800/30 border border-base-700">
                    <div>
                      <p className="text-sm text-base-100">{PRESET_META[role]?.label ?? role}</p>
                      <p className="text-xs text-base-500">{PRESET_META[role]?.desc ?? ''}</p>
                    </div>
                    <select
                      value={currentModel}
                      onChange={(e) => setRoleModel(role, e.target.value)}
                      className="min-w-[180px] bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono outline-none focus:border-accent/40"
                    >
                      <option value="">Use default</option>
                      {modelOptions.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
