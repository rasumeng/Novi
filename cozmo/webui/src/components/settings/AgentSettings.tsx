import { Bot, Thermometer, ListOrdered, MessageSquareText } from 'lucide-react'
import type { SettingsData } from './types'
import type { AgentConfig } from '@/types'

interface Props {
  config: SettingsData | null
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
}

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
    <div className="space-y-5">
      <p className="text-xs text-base-500">Fine-tune how Cozmo behaves when working autonomously. Most people won't need to change these.</p>

      <div className="space-y-2">
        <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
          <div className="flex items-center gap-2">
            <Bot size={14} className="text-accent" />
            <div>
              <p className="text-sm text-base-100 font-medium">Agent Model</p>
              <p className="text-xs text-base-500">
                {agentModel
                  ? `Using "${agentModel}" — change in Models tab`
                  : 'No model set — configure in Models tab'}
              </p>
            </div>
          </div>
          <span className="text-xs text-base-400 font-mono">{agentModel || '—'}</span>
        </div>

        <div className="p-3 rounded-xl bg-base-800/50 border border-base-700">
          <div className="flex items-center gap-2 mb-2">
            <MessageSquareText size={14} className="text-accent" />
            <p className="text-sm text-base-100 font-medium">System Prompt</p>
          </div>
          <p className="text-xs text-base-500 mb-2">Extra instructions Cozmo follows whenever it's working autonomously.</p>
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
              <p className="text-sm text-base-100 font-medium">Max Steps</p>
              <p className="text-xs text-base-500">How many actions Cozmo can take in a row before it stops to check in</p>
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
  )
}
