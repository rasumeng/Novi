import { useState, useEffect } from 'react'
import { Trash2, Brain } from 'lucide-react'
import { API_BASE } from './api'
import { useToast } from '@/hooks/useToast'
import { useConfirm } from '@/hooks/useConfirm'
import { KnowledgeOverview } from '@/components/knowledge/KnowledgeOverview'
import { useFrameworkSettings } from '@/hooks/useFrameworkSettings'

interface Props {
  framework: ReturnType<typeof useFrameworkSettings>
}

export function MemorySettings({ framework }: Props) {
  const { showError } = useToast()
  const { confirm, dialog } = useConfirm()
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [allMemory, setAllMemory] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<'overview' | 'preferences' | 'dev'>('overview')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const [memoryError, setMemoryError] = useState<string | null>(null)
  const [memoryStatus, setMemoryStatus] = useState<'ok' | 'disabled' | 'unavailable'>('ok')
  const memoryEnabled = (framework.values['memory.enabled'] as boolean) ?? true
  const unwrap = (j: any): { data: any[]; error?: string; status?: string } => {
    if (Array.isArray(j)) return { data: j, status: 'ok' }
    if (j && Array.isArray(j.data)) return { data: j.data, error: j.error, status: j.status }
    return { data: [], error: j?.error, status: j?.status }
  }
  const fetchAll = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/memory/list`)
      const j = await r.json()
      const { data, error, status } = unwrap(j)
      if (status === 'disabled') {
        setMemoryStatus('disabled')
        setMemoryError(null)
      } else if (status === 'unavailable') {
        setMemoryStatus('unavailable')
        setMemoryError(error || 'Brain store unavailable — check logs')
      } else {
        setMemoryStatus('ok')
        setMemoryError(null)
      }
      setAllMemory(data)
    } catch {
      setMemoryStatus('unavailable')
      setMemoryError('Brain store unavailable — check logs')
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
      const j = await r.json()
      const { data, error, status } = unwrap(j)
      if (status === 'unavailable') setMemoryError(error || 'Brain store unavailable — check logs')
      setSearchResults(data)
    } catch {
      showError('Memory search failed.')
    }
    setLoading(false)
  }

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: 'Delete this memory?',
      description: "Novi won't be able to recall this anymore. This can't be undone.",
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

  const handleClearAll = async () => {
    const ok = await confirm({
      title: 'Delete all memories?',
      description: "Novi won't be able to recall any of these anymore. This can't be undone.",
      confirmLabel: 'Delete all',
    })
    if (!ok) return
    try {
      const r = await fetch(`${API_BASE}/api/memory`, { method: 'DELETE' })
      if (!r.ok) throw new Error('request failed')
      setAllMemory([])
      setSearchResults([])
    } catch {
      showError("Couldn't delete memories.")
    }
  }

  const setMemoryPref = (key: 'memory.max_turns_before_summary' | 'memory.max_short_term_pairs', value: number) => {
    void framework.set(key, value)
  }

  useEffect(() => {
    fetchAll()
  }, [])

  return (
    <div className="space-y-4">
      {dialog}
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl bg-accent/15 text-accent flex items-center justify-center shrink-0">
          <Brain size={17} />
        </div>
        <div>
          <p className="text-sm text-base-100 font-medium">Memory</p>
          <p className="text-xs text-base-500 mt-0.5">Browse stored context and tune how Novi remembers.</p>
        </div>
      </div>
      <p className="text-xs text-base-500">Novi remembers useful details from past conversations, so it doesn't have to be told twice.</p>

      <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
        <div>
          <p className="text-sm text-base-100">Remember details from conversations</p>
          <p className="text-xs text-base-500">When off, Novi won't store new memories or recall old ones</p>
        </div>
        <button
          role="switch"
          aria-checked={memoryEnabled}
          onClick={() => void framework.set('memory.enabled', !memoryEnabled)}
          className={`relative w-10 h-6 rounded-full transition-colors shrink-0 ${memoryEnabled ? 'bg-accent' : 'bg-base-600'}`}
        >
          <span
            className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-all ${memoryEnabled ? 'left-[18px]' : 'left-0.5'}`}
          />
        </button>
      </div>

      {!memoryEnabled && (
        <div className="rounded-xl border border-base-700 bg-base-800/50 px-3 py-2.5 text-center">
          <p className="text-xs font-medium text-base-200">Memory is off</p>
          <p className="text-[11px] text-base-500 mt-0.5">Turn it on to let Novi store and recall details again. Existing memories are kept.</p>
        </div>
      )}
      {memoryEnabled && memoryStatus === 'unavailable' && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-center">
          <p className="text-xs font-medium text-amber-300">Brain store unavailable — check logs</p>
          {memoryError && <p className="text-[11px] text-base-500 mt-0.5">{memoryError}</p>}
        </div>
      )}

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
        {showAdvanced && (
          <button
            onClick={() => setTab('dev')}
            className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              tab === 'dev' ? 'bg-base-700 text-base-100' : 'text-base-400 hover:text-base-200'
            }`}
          >
            Advanced
          </button>
        )}
      </div>

      {!showAdvanced && (
        <button
          onClick={() => setShowAdvanced(true)}
          className="w-full rounded-xl border border-base-700 bg-base-800/30 px-3 py-2.5 text-left transition-colors hover:bg-base-800/60"
        >
          <span className="block text-xs font-medium text-base-300">Memory troubleshooting</span>
          <span className="mt-0.5 block text-[11px] text-base-500">Search the raw index and manage individual stored memories.</span>
          <span className="mt-1.5 block text-[11px] font-medium text-accent">Show advanced controls</span>
        </button>
      )}

      {tab === 'overview' && (
        <KnowledgeOverview />
      )}

      {tab === 'dev' && (
        <div className="space-y-3">
          {memoryError && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-center">
              <p className="text-xs font-medium text-amber-300">Brain store unavailable — check logs</p>
              <p className="text-[11px] text-base-500 mt-0.5">{memoryError}</p>
            </div>
          )}
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
            <div className="flex items-center justify-between">
              <p className="text-[11px] text-base-400 font-medium">All stored items ({allMemory.length})</p>
              {allMemory.length > 0 && (
                <button
                  onClick={handleClearAll}
                  className="px-2 py-1 text-[11px] font-medium rounded-lg bg-base-700 text-base-200 hover:text-err transition-colors"
                >
                  Delete all
                </button>
              )}
            </div>
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
              <p className="text-sm text-base-100">How long before Novi summarizes</p>
              <p className="text-xs text-base-500">Turns in a conversation before older parts get condensed into a memory</p>
            </div>
            <input
              type="number"
              min={1}
              value={(framework.values['memory.max_turns_before_summary'] as number) ?? 5}
              onChange={(e) => setMemoryPref('memory.max_turns_before_summary', Math.max(1, parseInt(e.target.value) || 1))}
              className="w-16 bg-base-900 border border-base-700 rounded-lg px-2 py-1.5 text-sm text-base-200 text-right outline-none focus:border-accent/40"
            />
          </div>
          <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div>
              <p className="text-sm text-base-100">Recent context Novi keeps handy</p>
              <p className="text-xs text-base-500">How many recent exchanges stay immediately available, without needing to be recalled</p>
            </div>
            <input
              type="number"
              min={1}
              value={(framework.values['memory.max_short_term_pairs'] as number) ?? 10}
              onChange={(e) => setMemoryPref('memory.max_short_term_pairs', Math.max(1, parseInt(e.target.value) || 1))}
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
