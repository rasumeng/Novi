import { useState, useEffect } from 'react'
import { Trash2 } from 'lucide-react'
import { API_BASE } from './api'
import type { SettingsData } from './types'
import { useToast } from '@/hooks/useToast'
import { useConfirm } from '@/hooks/useConfirm'
import { KnowledgeOverview } from '@/components/knowledge/KnowledgeOverview'

interface Props {
  config: SettingsData | null
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
}

export function MemorySettings({ config, setConfig, setDirty }: Props) {
  const { showError } = useToast()
  const { confirm, dialog } = useConfirm()
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [allMemory, setAllMemory] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<'overview' | 'preferences' | 'dev'>('overview')

  const fetchAll = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/memory/list`)
      const data = await r.json()
      setAllMemory(data)
    } catch {
      showError("Couldn't load stored memories.")
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults([])
      return
    }
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/api/memory/search?q=${encodeURIComponent(searchQuery)}`)
      const data = await r.json()
      setSearchResults(data)
    } catch {
      showError('Memory search failed.')
    }
    setLoading(false)
  }

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: 'Delete this memory?',
      description: "Cozmo won't be able to recall this anymore. This can't be undone.",
      confirmLabel: 'Delete',
    })
    if (!ok) return
    try {
      const r = await fetch(`${API_BASE}/api/memory/${id}`, { method: 'DELETE' })
      if (!r.ok) throw new Error('request failed')
      setAllMemory(prev => prev.filter(m => m.id !== id))
      setSearchResults(prev => prev.filter(m => m.id !== id))
    } catch {
      showError("Couldn't delete this memory.")
    }
  }

  const setMemoryPref = (key: 'max_turns_before_summary' | 'max_short_term_pairs', value: number) => {
    if (!config) return
    setConfig({ ...config, memory: { ...config.memory, [key]: value } })
    setDirty(true)
  }

  useEffect(() => {
    fetchAll()
  }, [])

  return (
    <div className="space-y-4">
      {dialog}
      <p className="text-xs text-base-500">Cozmo remembers useful details from past conversations, so it doesn't have to be told twice.</p>

      <div className="flex gap-1 p-0.5 bg-base-800 rounded-lg">
        <button
          onClick={() => setTab('overview')}
          className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
            tab === 'overview' ? 'bg-base-700 text-base-100' : 'text-base-400 hover:text-base-200'
          }`}
        >
          What I know
        </button>
        <button
          onClick={() => setTab('preferences')}
          className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
            tab === 'preferences' ? 'bg-base-700 text-base-100' : 'text-base-400 hover:text-base-200'
          }`}
        >
          Preferences
        </button>
        <button
          onClick={() => setTab('dev')}
          className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
            tab === 'dev' ? 'bg-base-700 text-base-100' : 'text-base-400 hover:text-base-200'
          }`}
        >
          Developer
        </button>
      </div>

      {tab === 'overview' && (
        <KnowledgeOverview />
      )}

      {tab === 'dev' && (
        <div className="space-y-3">
          <p className="text-xs text-base-500">Diagnostic view of the raw memory index. This is a troubleshooting surface — most people only need the “What I know” tab.</p>
          <div className="flex gap-2">
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="Search memories..."
              className="flex-1 bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40"
            />
            <button
              onClick={handleSearch}
              disabled={loading}
              className="px-3 py-2 text-xs font-medium rounded-lg bg-base-700 text-base-200 hover:bg-base-600 transition-colors disabled:opacity-50"
            >
              {loading ? '...' : 'Search'}
            </button>
          </div>

          {searchResults.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[11px] text-base-400 font-medium">Search results ({searchResults.length})</p>
              {searchResults.map((item, i) => (
                <MemoryCard key={item.id || i} item={item} onDelete={handleDelete} />
              ))}
            </div>
          )}

          <div className="space-y-1.5">
            <p className="text-[11px] text-base-400 font-medium">All stored items ({allMemory.length})</p>
            {allMemory.length === 0 && (
              <p className="text-xs text-base-500 py-4 text-center">No memories stored yet. Memories are created automatically from conversations.</p>
            )}
            {allMemory.slice(0, 50).map((item) => (
              <MemoryCard key={item.id} item={item} onDelete={handleDelete} />
            ))}
          </div>
        </div>
      )}

      {tab === 'preferences' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div>
              <p className="text-sm text-base-100">How long before Cozmo summarizes</p>
              <p className="text-xs text-base-500">Turns in a conversation before older parts get condensed into a memory</p>
            </div>
            <input
              type="number"
              min={1}
              value={config?.memory?.max_turns_before_summary ?? 5}
              onChange={(e) => setMemoryPref('max_turns_before_summary', Math.max(1, parseInt(e.target.value) || 1))}
              className="w-16 bg-base-900 border border-base-700 rounded-lg px-2 py-1.5 text-sm text-base-200 text-right outline-none focus:border-accent/40"
            />
          </div>
          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div>
              <p className="text-sm text-base-100">Recent context Cozmo keeps handy</p>
              <p className="text-xs text-base-500">How many recent exchanges stay immediately available, without needing to be recalled</p>
            </div>
            <input
              type="number"
              min={1}
              value={config?.memory?.max_short_term_pairs ?? 10}
              onChange={(e) => setMemoryPref('max_short_term_pairs', Math.max(1, parseInt(e.target.value) || 1))}
              className="w-16 bg-base-900 border border-base-700 rounded-lg px-2 py-1.5 text-sm text-base-200 text-right outline-none focus:border-accent/40"
            />
          </div>
        </div>
      )}
    </div>
  )
}

function MemoryCard({ item, onDelete }: { item: any; onDelete: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const text = item.text || ''
  const preview = text.length > 120 ? text.slice(0, 120) + '...' : text
  const meta = item.metadata || {}

  return (
    <div className="p-2.5 rounded-lg bg-base-800/30 border border-base-700/50 group">
      <div className="flex items-start justify-between gap-2">
        <div
          className="flex-1 min-w-0 cursor-pointer"
          onClick={() => setExpanded(!expanded)}
        >
          <p className="text-xs text-base-200 leading-relaxed whitespace-pre-wrap">
            {expanded ? text : preview}
          </p>
          <div className="flex items-center gap-2 mt-1.5">
            {meta.timestamp && (
              <span className="text-[10px] text-base-500">{new Date(meta.timestamp).toLocaleDateString()}</span>
            )}
            {meta.turns && (
              <span className="text-[10px] text-base-600">{meta.turns} turns</span>
            )}
          </div>
        </div>
        <button
          onClick={() => onDelete(item.id)}
          className="p-1 rounded text-base-600 hover:text-err opacity-0 group-hover:opacity-100 transition-all"
          title="Delete memory"
        >
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  )
}
