import { Server, KeyRound } from 'lucide-react'
import type { SettingsData } from './types'
import type { ModelCatalogEntry } from '@/product/types'
import { roleModelPatch, embeddingModelPatch } from '@/product/configLayer'
import { ModelRolePicker } from './ModelRolePicker'
import { ExpertModelConfig } from './ExpertModelConfig'

interface Props {
  config: SettingsData | null
  catalog: ModelCatalogEntry[]
  availableModels: { name: string; provider: string }[]
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
}

const allModelOptions = (models: { name: string; provider: string }[]): string[] => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const m of models) {
    if (!seen.has(m.name)) {
      seen.add(m.name)
      out.push(m.name)
    }
  }
  return out
}

export function ModelsSettings({ config, catalog, availableModels, setConfig, setDirty }: Props) {
  if (!config) return null

  const llmRoles: Record<string, any> = config.llm?.roles || {}
  const roleModel = (role: string) => {
    const spec = llmRoles[role]
    return (typeof spec === 'string' ? spec : spec?.model) || ''
  }

  const setRoleModel = (role: string, modelId: string) => {
    setConfig({ ...config, ...roleModelPatch(config, role, modelId) })
    setDirty(true)
  }

  const setEmbeddingModel = (modelId: string) => {
    setConfig({ ...config, ...embeddingModelPatch(config, modelId) })
    setDirty(true)
  }

  const providers = config.providers || {}
  const ollamaUrl = providers.ollama?.url || config.ollama?.url || 'http://localhost:11434'
  const openaiKeyEnv = providers.openai?.api_key_env || 'OPENAI_API_KEY'
  const ollamaReasoning = (providers.ollama as { reasoning?: boolean } | undefined)?.reasoning !== false

  const setOllamaUrl = (url: string) => {
    setConfig({
      ...config,
      ollama: { ...(config.ollama || {}), url },
      providers: { ...providers, default: providers.default || 'ollama', ollama: { ...providers.ollama, url } },
    })
    setDirty(true)
  }

  const setOllamaReasoning = (enabled: boolean) => {
    setConfig({
      ...config,
      providers: { ...providers, default: providers.default || 'ollama', ollama: { ...providers.ollama, url: ollamaUrl, reasoning: enabled } },
    })
    setDirty(true)
  }

  const setOpenaiKeyEnv = (env: string) => {
    setConfig({
      ...config,
      providers: { ...providers, default: providers.default || 'ollama', openai: { api_key_env: env } },
    })
    setDirty(true)
  }

  const setDefaultProvider = (provider: string) => {
    setConfig({ ...config, providers: { ...providers, default: provider } })
    setDirty(true)
  }

  return (
    <div className="space-y-6">
      <div className="space-y-5">
        <ModelRolePicker
          label="Conversation Model"
          description="Used for everyday chat and questions."
          capability="chat"
          currentModelId={roleModel('chat')}
          catalog={catalog}
          onChange={(id) => setRoleModel('chat', id)}
        />
        <ModelRolePicker
          label="Coding Model"
          description="Used when Cozmo writes or edits code."
          capability="coding"
          currentModelId={roleModel('coder')}
          catalog={catalog}
          onChange={(id) => setRoleModel('coder', id)}
        />
        <ModelRolePicker
          label="Vision Model"
          description="Used to understand images you share."
          capability="vision"
          currentModelId={roleModel('vision')}
          catalog={catalog}
          onChange={(id) => setRoleModel('vision', id)}
        />
        <ModelRolePicker
          label="Embedding Model"
          description="Powers Cozmo's memory and search in the background."
          capability="embeddings"
          currentModelId={(config.embedding as { model?: string } | undefined)?.model || ''}
          catalog={catalog}
          onChange={setEmbeddingModel}
        />
      </div>

      {/* Providers */}
      <div>
        <p className="text-sm text-base-100 font-medium mb-1">Providers</p>
        <p className="text-xs text-base-500 mb-2">Where Cozmo gets its models from.</p>
        <div className="space-y-2">
          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <span className="text-sm text-base-200">Default Provider</span>
            <select
              value={providers.default || 'ollama'}
              onChange={(e) => setDefaultProvider(e.target.value)}
              className="bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40"
            >
              {Object.keys(providers).filter((p) => p !== 'default').map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
              {!Object.keys(providers).some((p) => p !== 'default') && (
                <option value="ollama">ollama</option>
              )}
            </select>
          </div>

          <div className="space-y-2 p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Server size={14} className="text-base-500" />
                <span className="text-sm text-base-200">Ollama</span>
              </div>
              <input
                value={ollamaUrl}
                onChange={(e) => setOllamaUrl(e.target.value)}
                placeholder="http://localhost:11434"
                className="w-56 bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono outline-none focus:border-accent/40"
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-base-400">Show its thinking before answering</span>
              <button
                type="button"
                role="switch"
                aria-checked={ollamaReasoning}
                onClick={() => setOllamaReasoning(!ollamaReasoning)}
                className={`relative inline-flex h-4 w-8 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ${
                  ollamaReasoning ? 'bg-accent' : 'bg-base-700'
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-3 w-3 translate-x-0 rounded-full bg-white shadow ring-0 transition-transform duration-200 ${
                    ollamaReasoning ? 'translate-x-4' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div className="flex items-center gap-2">
              <KeyRound size={14} className="text-base-500" />
              <span className="text-sm text-base-200">OpenAI</span>
            </div>
            <input
              value={openaiKeyEnv}
              onChange={(e) => setOpenaiKeyEnv(e.target.value)}
              placeholder="OPENAI_API_KEY"
              className="w-56 bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono outline-none focus:border-accent/40"
            />
          </div>
        </div>
      </div>

      <ExpertModelConfig
        config={config}
        modelOptions={allModelOptions(availableModels)}
        setConfig={setConfig}
        setDirty={setDirty}
      />
    </div>
  )
}
