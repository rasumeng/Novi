import { useState, useEffect, useMemo } from 'react'
import {
  X,
  Search,
  Store,
  Plug,
  PackagePlus,
  Settings,
  Power,
  Trash2,
  ChevronDown,
  Puzzle,
  Cable,
  Activity,
  Globe,
  Wrench,
  AlertTriangle,
  Check,
  Layers,
  Filter,
} from 'lucide-react'
import { fetchMcpCatalog, fetchMcpStatus, fetchServerDetail } from '@/services/novi'
import type { McpCatalogEntry, McpStatusResponse, McpServerTool, McpServerDetail } from '@/types'
import { API_BASE } from './api'
import { CAPABILITY_DEFS, PERMISSION_DEFS } from './constants'
import type { SettingsData } from './types'
import { useConfirm } from '@/hooks/useConfirm'
import { CapabilityBadge } from '@/components/common/CapabilityBadge'

function formatTimeAgo(ms: number): string {
  const sec = Math.round(ms / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  return `${hr}h ago`
}

interface Props {
  config: SettingsData | null
  setConfig: (c: SettingsData) => void
  setDirty: (d: boolean) => void
}

interface SearchConfigShape {
  backend?: string
  brave_api_key?: string
  url?: string
}

type SearchTestState = { state: string; message: string }

const SEARCH_STATE_STYLE: Record<string, { pill: string; label: string }> = {
  connected: { pill: 'text-emerald-300 bg-emerald-500/10 border border-emerald-500/20', label: 'Connected' },
  auth_failed: { pill: 'text-red-300 bg-red-500/10 border border-red-500/20', label: 'Authentication failed' },
  unavailable: { pill: 'text-red-300 bg-red-500/10 border border-red-500/20', label: 'Unavailable' },
  rate_limited: { pill: 'text-amber-300 bg-amber-500/10 border border-amber-500/20', label: 'Rate limited' },
  not_configured: { pill: 'text-base-400 bg-base-800 border border-base-600', label: 'Not configured' },
  unknown_error: { pill: 'text-red-300 bg-red-500/10 border border-red-500/20', label: 'Unknown error' },
}

// ─────────────────────────────────────────────────────────────────────────────
// Web Search — isolated module, not mixed with MCP
// ─────────────────────────────────────────────────────────────────────────────
export function WebSearchCard({ config, setConfig, setDirty }: Props) {
  const search: SearchConfigShape = ((config as any)?.search ?? {}) as SearchConfigShape
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<SearchTestState | null>(null)

  const setSearchField = (key: keyof SearchConfigShape, value: string) => {
    if (!config) return
    const current = ((config as any).search ?? {}) as Record<string, unknown>
    setConfig({ ...config, search: { ...current, [key]: value } } as SettingsData)
    setDirty(true)
  }

  const runTest = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await fetch(`${API_BASE}/api/search/test`, { method: 'POST' })
      setResult(await r.json())
    } catch {
      setResult({ state: 'unknown_error', message: 'Could not reach the Novi server.' })
    } finally {
      setTesting(false)
    }
  }

  const stateStyle = result ? SEARCH_STATE_STYLE[result.state] ?? SEARCH_STATE_STYLE.unknown_error : null
  const hasProvider = !!search.backend

  return (
    <div className="rounded-2xl border border-base-700/60 bg-base-900/40 overflow-hidden">
      {/* header rail */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-base-700/40 bg-base-800/20">
        <div className="w-8 h-8 rounded-xl bg-accent/10 border border-accent/15 flex items-center justify-center shrink-0">
          <Globe size={15} className="text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold tracking-tight text-base-100 leading-none">Web Search</p>
          <p className="text-[11px] text-base-500 mt-0.5">Novi uses this when it needs current information</p>
        </div>
        {stateStyle ? (
          <span className={`shrink-0 text-[10px] font-medium px-2.5 py-1 rounded-full ${stateStyle.pill}`}>{stateStyle.label}</span>
        ) : (
          <span className={`shrink-0 text-[10px] font-medium px-2.5 py-1 rounded-full ${hasProvider ? 'text-amber-300 bg-amber-500/10 border border-amber-500/20' : 'text-base-400 bg-base-800 border border-base-600'}`}>
            {hasProvider ? 'Not tested' : 'Not configured'}
          </span>
        )}
      </div>

      <div className="p-4 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="block">
            <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">Provider</span>
            <select
              value={search.backend ?? ''}
              onChange={(e) => setSearchField('backend', e.target.value)}
              className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 transition-colors"
            >
              <option value="">Not configured</option>
              <option value="brave">Brave Search</option>
              <option value="searxng">SearXNG (self-hosted)</option>
            </select>
          </label>

          {search.backend === 'brave' && (
            <label className="block">
              <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">Brave API key</span>
              <input
                type="password"
                value={search.brave_api_key ?? ''}
                onChange={(e) => setSearchField('brave_api_key', e.target.value)}
                placeholder="BSA …"
                autoComplete="off"
                className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 font-mono transition-colors"
              />
            </label>
          )}

          {search.backend === 'searxng' && (
            <label className="block">
              <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">SearXNG endpoint</span>
              <input
                type="text"
                value={search.url ?? ''}
                onChange={(e) => setSearchField('url', e.target.value)}
                placeholder="http://localhost:8080"
                className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 font-mono transition-colors"
              />
            </label>
          )}
        </div>

        {search.backend === 'searxng' && (
          <p className="text-[11px] leading-relaxed text-base-500 bg-base-800/50 border border-base-700/30 rounded-xl px-3 py-2">
            Requires Docker or an existing SearXNG instance with JSON format enabled.
          </p>
        )}
        {search.backend === 'brave' && !search.brave_api_key && (
          <p className="text-[11px] leading-relaxed text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-xl px-3 py-2">
            Enter your Brave Search API key to enable web search. Get one at <span className="underline decoration-amber-400/30">brave.com/search/api</span>.
          </p>
        )}

        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-[11px] text-base-500 hidden sm:block">Test verifies the provider before Novi uses it.</p>
          <button
            onClick={runTest}
            disabled={!search.backend || testing}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium bg-accent hover:bg-accent/90 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-accent/30 ml-auto"
          >
            <Activity size={13} /> {testing ? 'Testing…' : 'Test connection'}
          </button>
        </div>

        {result && (
          <div
            className={`px-3 py-2.5 rounded-xl text-xs leading-relaxed ${
              result.state === 'connected'
                ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
                : result.state === 'rate_limited'
                  ? 'bg-amber-500/10 text-amber-300 border border-amber-500/20'
                  : 'bg-red-500/10 text-red-300 border border-red-500/20'
            }`}
          >
            {result.message}
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Patch-bay header — the signature element (one bold place)
// ─────────────────────────────────────────────────────────────────────────────
function PatchbayHeader({
  connected,
  total,
  tools,
  capabilities,
  searchReady,
}: {
  connected: number
  total: number
  tools: number
  capabilities: number
  searchReady: boolean
}) {
  const allOk = total > 0 && connected === total
  return (
    <div className="rounded-2xl border border-base-700/60 bg-base-900/30 overflow-hidden">
      {/* top bar */}
      <div className="flex items-center justify-between px-4 sm:px-5 py-3 border-b border-base-700/40">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center">
            <Cable size={16} className="text-accent" />
          </div>
          <div>
            <p className="text-[13px] font-semibold tracking-tight text-base-100">Connectors</p>
            <p className="text-[11px] text-base-500">External tools and services Novi can use</p>
          </div>
        </div>
        <span className="hidden sm:inline-flex items-center gap-1.5 text-[10px] font-medium tracking-wider uppercase px-2.5 py-1 rounded-full border bg-base-800 text-base-400 border-base-600">
          <span className={`w-1.5 h-1.5 rounded-full ${allOk ? 'bg-emerald-400' : 'bg-amber-400'} ${allOk ? 'animate-pulse' : ''}`} />
          {allOk ? 'All systems patched' : total === 0 ? 'No patches yet' : `${connected}/${total} live`}
        </span>
      </div>

      {/* metrics rail + patch visual */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-base-700/40">
        <div className="bg-base-900/50 px-4 py-3.5">
          <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-1">Live</p>
          <p className="text-xl font-semibold tracking-tight text-base-100 leading-none">
            {connected}
            <span className="text-base-500 font-normal"> / {total}</span>
          </p>
          <p className="text-[11px] text-base-500 mt-1">connectors online</p>
        </div>
        <div className="bg-base-900/50 px-4 py-3.5">
          <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-1">Tools</p>
          <p className="text-xl font-semibold tracking-tight text-base-100 leading-none">{tools}</p>
          <p className="text-[11px] text-base-500 mt-1">exposed to Novi</p>
        </div>
        <div className="bg-base-900/50 px-4 py-3.5">
          <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-1">Capabilities</p>
          <p className="text-xl font-semibold tracking-tight text-base-100 leading-none">{capabilities}</p>
          <p className="text-[11px] text-base-500 mt-1">distinct abilities</p>
        </div>
        <div className="bg-base-900/50 px-4 py-3.5">
          <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-1">Web search</p>
          <p className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full border mt-0.5 ${searchReady ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20' : 'text-base-400 bg-base-800 border-base-600'}`}>
            {searchReady ? <Check size={12} /> : <AlertTriangle size={12} />} {searchReady ? 'Ready' : 'Not set'}
          </p>
        </div>
      </div>

      {/* subtle socket row — signature patch-bay */}
      <div className="px-4 sm:px-5 py-3 bg-base-950/40 flex items-center gap-2 overflow-x-auto">
        <span className="text-[10px] font-medium tracking-wider uppercase text-base-500 shrink-0 mr-1">Patch bay</span>
        <div className="flex items-center gap-2">
          {Array.from({ length: Math.max(6, Math.min(12, total + 4)) }).map((_, i) => {
            const isLive = i < connected
            const isMid = i < total
            return (
              <div key={i} className="flex items-center gap-2 shrink-0">
                <div
                  className={`w-7 h-7 rounded-full border flex items-center justify-center ${
                    isLive
                      ? 'bg-accent/15 border-accent/30 text-accent'
                      : isMid
                        ? 'bg-amber-500/10 border-amber-500/20 text-amber-400'
                        : 'bg-base-800 border-base-700 text-base-500'
                  }`}
                  title={isLive ? 'Live' : isMid ? 'Offline' : 'Empty socket'}
                >
                  <span className={`w-2 h-2 rounded-full ${isLive ? 'bg-accent animate-pulse' : isMid ? 'bg-amber-400' : 'bg-base-600'}`} />
                </div>
                {i < Math.max(6, Math.min(12, total + 4)) - 1 && (
                  <div className={`w-6 h-px ${i < connected - 1 ? 'bg-accent/30' : i < total - 1 ? 'bg-base-700' : 'bg-base-700/40 border-t border-dashed border-base-700'}`} />
                )}
              </div>
            )
          })}
        </div>
        <span className="ml-auto hidden sm:block text-[11px] text-base-500 shrink-0">Sockets light when a connector is live</span>
      </div>
    </div>
  )
}

export function ConnectorsSection({ config, setConfig, setDirty }: Props) {
  const { confirm, dialog } = useConfirm()
  const [addOpen, setAddOpen] = useState(false)
  const [addName, setAddName] = useState('')
  const [addCommand, setAddCommand] = useState('')
  const [addArgs, setAddArgs] = useState('')
  const [addEnv, setAddEnv] = useState<string>('')
  const [testResult, setTestResult] = useState<string | null>(null)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [catalog, setCatalog] = useState<McpCatalogEntry[]>([])
  const [catalogSearch, setCatalogSearch] = useState('')
  const [selectedCatalog, setSelectedCatalog] = useState<McpCatalogEntry | null>(null)
  const [catalogEnvVars, setCatalogEnvVars] = useState<Record<string, string>>({})
  const [serverStatus, setServerStatus] = useState<McpStatusResponse | null>(null)
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({})
  const [detailName, setDetailName] = useState<string | null>(null)
  const [serverDetail, setServerDetail] = useState<McpServerDetail | null>(null)
  const [installedQuery, setInstalledQuery] = useState('')
  const [capFilter, setCapFilter] = useState<string | null>(null)

  const devMode = !!(config as any)?.devMode
  const servers = (config?.mcp as { servers?: Record<string, { command: string; args?: string[]; env?: Record<string, string>; permissions?: Record<string, boolean> }> })?.servers ?? {}
  const entries = Object.entries(servers)

  useEffect(() => {
    const poll = async () => {
      setServerStatus(await fetchMcpStatus())
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [config])

  const catalogByName = useMemo(() => {
    const map: Record<string, McpCatalogEntry> = {}
    for (const e of catalog) {
      map[e.display_name] = e
      map[e.id] = e
    }
    return map
  }, [catalog])

  const activeCapabilities = useMemo(() => {
    const caps = new Set<string>()
    for (const name of Object.keys(servers)) {
      const entry = catalogByName[name]
      if (entry) {
        for (const c of entry.capabilities) caps.add(c)
      }
    }
    return caps
  }, [servers, catalogByName])

  const serverCapabilities = useMemo(() => {
    const map: Record<string, string[]> = {}
    for (const name of Object.keys(servers)) {
      const entry = catalogByName[name]
      map[name] = entry ? [...entry.capabilities] : []
    }
    return map
  }, [servers, catalogByName])

  const totalTools = useMemo(() => {
    if (!serverStatus) return 0
    return Object.values(serverStatus).reduce((n, s) => n + (s.tools?.length ?? 0), 0)
  }, [serverStatus])

  const connectedCount = useMemo(() => {
    if (!serverStatus) return 0
    return Object.values(serverStatus).filter((s) => s.status === 'ok').length
  }, [serverStatus])

  const filteredInstalled = useMemo(() => {
    let list = entries
    if (installedQuery) {
      const q = installedQuery.toLowerCase()
      list = list.filter(([name, cfg]) => {
        const entry = catalogByName[name]
        return (
          name.toLowerCase().includes(q) ||
          (entry?.description ?? '').toLowerCase().includes(q) ||
          (entry?.category ?? '').toLowerCase().includes(q) ||
          `${cfg.command} ${cfg.args?.join(' ') ?? ''}`.toLowerCase().includes(q)
        )
      })
    }
    if (capFilter) {
      list = list.filter(([name]) => (serverCapabilities[name] ?? []).includes(capFilter))
    }
    return list
  }, [entries, installedQuery, capFilter, catalogByName, serverCapabilities])

  const openCatalog = async () => {
    const data = await fetchMcpCatalog()
    setCatalog(data)
    setCatalogSearch('')
    setCatalogOpen(true)
  }

  const pickCatalog = (entry: McpCatalogEntry) => {
    setSelectedCatalog(entry)
    setAddName(entry.display_name || entry.id)
    setAddCommand(entry.command)
    setAddArgs(entry.args.join(', '))
    setCatalogOpen(false)
    const init: Record<string, string> = {}
    for (const ev of entry.env_vars) {
      init[ev.key] = ev.default || ''
    }
    setCatalogEnvVars(init)
    setAddOpen(true)
  }

  const clearForm = () => {
    setAddName('')
    setAddCommand('')
    setAddArgs('')
    setAddEnv('')
    setSelectedCatalog(null)
    setCatalogEnvVars({})
  }

  const handleAdd = () => {
    if (!addName.trim() || !addCommand.trim() || !config) return
    const args = addArgs.trim() ? addArgs.split(',').map((s) => s.trim()).filter(Boolean) : undefined
    let env: Record<string, string> | undefined
    if (selectedCatalog && Object.keys(catalogEnvVars).length > 0) {
      env = { ...catalogEnvVars }
    }
    if (addEnv.trim()) {
      if (!env) env = {}
      for (const pair of addEnv.split(',')) {
        const eq = pair.indexOf('=')
        if (eq > 0) {
          env[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim()
        }
      }
    }
    if (env && !Object.keys(env).length) env = undefined
    const mcp = (config.mcp as any) ?? { servers: {} }
    setConfig({
      ...config,
      mcp: { ...mcp, servers: { ...mcp.servers, [addName.trim()]: { command: addCommand.trim(), args, env } } },
    })
    setDirty(true)
    setAddOpen(false)
    clearForm()
  }

  const handleDelete = async (name: string) => {
    const ok = await confirm({
      title: `Remove ${name}?`,
      description: 'Novi will lose access to any tools this connector provides. You can add it again later.',
      confirmLabel: 'Remove',
    })
    if (!ok || !config) return false
    const mcp = (config.mcp as any) ?? { servers: {} }
    const { [name]: _, ...rest } = mcp.servers
    setConfig({ ...config, mcp: { ...mcp, servers: rest } })
    setDirty(true)
    return true
  }

  const handleTest = async (name: string) => {
    setTestResult(null)
    try {
      const r = await fetch(`${API_BASE}/api/mcp/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      const data = await r.json()
      setTestResult(data.ok ? `Connected — ${data.tools ?? 0} tools` : `Failed: ${data.error}`)
    } catch {
      setTestResult('Connection error')
    }
    setTimeout(() => setTestResult(null), 4000)
  }

  const toggleTools = (name: string) => {
    setExpandedTools((prev) => ({ ...prev, [name]: !prev[name] }))
  }

  const openDetail = async (name: string) => {
    setDetailName(name)
    setServerDetail(await fetchServerDetail(name))
  }

  const closeDetail = () => {
    setDetailName(null)
    setServerDetail(null)
  }

  const setPermission = (serverName: string, permKey: string, value: boolean) => {
    if (!config) return
    const mcp = (config.mcp as any) ?? { servers: {} }
    const server = mcp.servers[serverName] ?? {}
    const perms = { ...server.permissions, [permKey]: value }
    setConfig({
      ...config,
      mcp: { ...mcp, servers: { ...mcp.servers, [serverName]: { ...server, permissions: perms } } },
    })
    setDirty(true)
  }

  const filteredCatalog = catalogSearch
    ? catalog.filter(
        (e) =>
          e.display_name.toLowerCase().includes(catalogSearch.toLowerCase()) ||
          e.description.toLowerCase().includes(catalogSearch.toLowerCase()) ||
          e.category.toLowerCase().includes(catalogSearch.toLowerCase())
      )
    : catalog

  const catalogGroups: Record<string, McpCatalogEntry[]> = {}
  for (const e of filteredCatalog) {
    if (!catalogGroups[e.category]) catalogGroups[e.category] = []
    catalogGroups[e.category].push(e)
  }

  const searchShape: SearchConfigShape = ((config as any)?.search ?? {}) as SearchConfigShape
  const searchReady = !!searchShape.backend && (searchShape.backend === 'searxng' ? !!searchShape.url : !!searchShape.brave_api_key)

  return (
    <div className="space-y-5">
      {dialog}

      {/* thesis header */}
      <PatchbayHeader
        connected={connectedCount}
        total={entries.length}
        tools={totalTools}
        capabilities={activeCapabilities.size}
        searchReady={searchReady}
      />

      {/* Web Search — distinct module */}
      <WebSearchCard config={config} setConfig={setConfig} setDirty={setDirty} />

      {/* Installed controls */}
      <div className="flex flex-col sm:flex-row gap-3 sm:items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-[13px] font-semibold tracking-tight text-base-100">
            Installed
            <span className="ml-2 text-[11px] font-normal text-base-500">
              {filteredInstalled.length !== entries.length ? `${filteredInstalled.length}/${entries.length}` : `${entries.length}`}
            </span>
          </h3>
          {capFilter && (
            <button
              onClick={() => setCapFilter(null)}
              className="inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent/80 bg-accent/10 border border-accent/20 px-2 py-1 rounded-full"
            >
              <Filter size={11} /> {CAPABILITY_DEFS[capFilter]?.label ?? capFilter} <X size={11} />
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="relative flex-1 sm:w-64">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-base-500" />
            <input
              value={installedQuery}
              onChange={(e) => setInstalledQuery(e.target.value)}
              placeholder="Filter installed…"
              className="w-full bg-base-800 border border-base-700 rounded-xl pl-9 pr-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 transition-colors"
            />
          </div>
          <button
            onClick={openCatalog}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-accent/30 text-accent hover:bg-accent/10 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20 shrink-0"
          >
            <Store size={14} /> Browse store
          </button>
          <button
            onClick={() => {
              clearForm()
              setAddOpen(true)
            }}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-accent/30 shrink-0"
          >
            <Plug size={14} /> Add manually
          </button>
        </div>
      </div>

      {/* capability filter chips — information as structure */}
      {entries.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(CAPABILITY_DEFS)
            .filter(([key]) => activeCapabilities.has(key))
            .map(([key, def]) => {
              const Icon = def.icon
              const active = capFilter === key
              return (
                <button
                  key={key}
                  onClick={() => setCapFilter(active ? null : key)}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-[11px] font-medium border transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20 ${
                    active
                      ? 'bg-accent text-white border-accent'
                      : 'bg-base-800 text-base-400 border-base-700 hover:border-accent/30 hover:text-base-200'
                  }`}
                >
                  <Icon size={12} /> {def.label}
                </button>
              )
            })}
          {activeCapabilities.size === 0 && <span className="text-[11px] text-base-500">No capabilities yet — add a connector to light up abilities</span>}
        </div>
      )}

      {/* active capability summary */}
      {entries.length > 0 && activeCapabilities.size > 0 && (
        <div className="rounded-xl border border-base-700/40 bg-base-900/20 px-4 py-3 flex flex-wrap gap-1.5 items-center">
          <Layers size={13} className="text-base-500 shrink-0" />
          <span className="text-[11px] font-medium tracking-wider uppercase text-base-500">Active capabilities</span>
          <span className="text-[11px] text-base-500">· {activeCapabilities.size} enabled</span>
          <div className="flex flex-wrap gap-1.5 ml-2">
            {Array.from(activeCapabilities).map((k) => {
              const cd = CAPABILITY_DEFS[k]
              if (!cd) return null
              return <CapabilityBadge key={k} icon={cd.icon} label={cd.label} />
            })}
          </div>
        </div>
      )}

      {testResult && (
        <div
          className={`px-4 py-2.5 rounded-xl text-xs border ${
            testResult.startsWith('Connected')
              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
              : 'bg-red-500/10 text-red-300 border-red-500/20'
          }`}
        >
          {testResult}
        </div>
      )}

      {/* installed list / empty */}
      {entries.length === 0 ? (
        <div className="rounded-2xl border-2 border-dashed border-base-700 bg-base-900/20 px-6 py-12 text-center">
          <div className="w-12 h-12 rounded-2xl bg-base-800 border border-base-700 flex items-center justify-center mx-auto mb-3">
            <Cable size={20} className="text-base-500" />
          </div>
          <p className="text-sm font-medium text-base-200">No connectors yet</p>
          <p className="text-xs text-base-500 mt-1 max-w-md mx-auto">Connect Novi to databases, APIs, file systems, and more. Start from the store or add a custom MCP server.</p>
          <div className="flex items-center justify-center gap-2 mt-4">
            <button onClick={openCatalog} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors">
              <Store size={14} /> Browse store
            </button>
            <button onClick={() => { clearForm(); setAddOpen(true) }} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl border border-base-700 bg-base-800 hover:bg-base-750 text-base-200 text-xs font-medium transition-colors">
              <Plug size={14} /> Add manually
            </button>
          </div>
        </div>
      ) : filteredInstalled.length === 0 ? (
        <div className="rounded-xl border border-base-700 bg-base-800/30 px-6 py-8 text-center">
          <p className="text-sm text-base-300">No connectors match</p>
          <p className="text-xs text-base-500 mt-1">Try a different search or clear the capability filter.</p>
          <button onClick={() => { setInstalledQuery(''); setCapFilter(null) }} className="mt-3 text-xs text-accent hover:text-accent/80">Clear filters</button>
        </div>
      ) : (
        <div className="space-y-2.5">
          {filteredInstalled.map(([name, cfg]) => {
            const st = serverStatus?.[name]
            const caps = serverCapabilities[name] ?? []
            const entry = catalogByName[name]
            const desc = entry?.description ?? `${cfg.command}${cfg.args ? ' ' + cfg.args.join(' ') : ''}`
            const needsToken = entry && entry.env_vars.some((ev) => !ev.optional) && (!cfg.env || Object.keys(cfg.env).length === 0)
            const isExpanded = !!expandedTools[name]
            return (
              <div key={name} className="rounded-2xl bg-base-800/40 border border-base-700 hover:border-base-600 overflow-hidden transition-colors group">
                <div className="px-4 py-3.5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-3 min-w-0 flex-1">
                      <span
                        className={`w-2.5 h-2.5 rounded-full shrink-0 mt-1.5 ${st?.status === 'ok' ? 'bg-emerald-400 shadow-[0_0_8px_rgba(74,154,122,0.5)]' : st?.status === 'error' ? 'bg-red-400' : 'bg-base-600'}`}
                        aria-label={st?.status === 'ok' ? 'Connected' : st?.status === 'error' ? 'Error' : 'Disconnected'}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <p className="text-[13px] font-semibold tracking-tight text-base-100 truncate">{name}</p>
                          {needsToken ? (
                            <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-medium text-amber-300 bg-amber-500/10 px-2 py-0.5 rounded-full border border-amber-500/20">
                              <AlertTriangle size={10} /> Needs token
                            </span>
                          ) : st?.status === 'ok' ? (
                            <span className="shrink-0 text-[10px] font-medium text-emerald-300 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">Connected</span>
                          ) : st?.status === 'error' ? (
                            <span className="shrink-0 text-[10px] font-medium text-red-300 bg-red-500/10 px-2 py-0.5 rounded-full border border-red-500/20">Error</span>
                          ) : (
                            <span className="shrink-0 text-[10px] font-medium text-base-400 bg-base-800 px-2 py-0.5 rounded-full border border-base-600">Disconnected</span>
                          )}
                        </div>
                        <p className="text-[11px] leading-relaxed text-base-500 line-clamp-1 mt-1">{desc}</p>
                        <div className="flex items-center gap-1.5 flex-wrap mt-2">
                          {caps.map((c) => {
                            const cd = CAPABILITY_DEFS[c]
                            if (!cd) return null
                            return <CapabilityBadge key={c} icon={cd.icon} label={cd.label} />
                          })}
                          {caps.length === 0 && <span className="text-[10px] text-base-500">No capabilities declared</span>}
                        </div>
                      </div>
                    </div>
                    <div className="hidden sm:flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => openDetail(name)}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-[11px] font-medium text-base-300 bg-base-800 hover:bg-base-700 border border-base-700 transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20"
                      >
                        <Settings size={12} /> Configure
                      </button>
                      <button
                        onClick={() => handleTest(name)}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-[11px] font-medium text-base-300 bg-base-800 hover:bg-base-700 border border-base-700 transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20"
                      >
                        <Power size={12} /> Test
                      </button>
                      <button
                        onClick={() => handleDelete(name)}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-[11px] font-medium text-base-400 hover:text-red-300 hover:bg-red-500/10 border border-base-700 hover:border-red-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-err/20"
                      >
                        <Trash2 size={12} /> Remove
                      </button>
                    </div>
                  </div>

                  {/* mobile actions — always visible */}
                  <div className="flex sm:hidden items-center gap-1 mt-3">
                    <button onClick={() => openDetail(name)} className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-2 rounded-xl text-[11px] font-medium text-base-300 bg-base-800 border border-base-700">
                      <Settings size={12} /> Configure
                    </button>
                    <button onClick={() => handleTest(name)} className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-2 rounded-xl text-[11px] font-medium text-base-300 bg-base-800 border border-base-700">
                      <Power size={12} /> Test
                    </button>
                    <button onClick={() => handleDelete(name)} className="inline-flex items-center justify-center gap-1 px-3 py-2 rounded-xl text-[11px] font-medium text-red-300 bg-red-500/10 border border-red-500/20">
                      <Trash2 size={12} />
                    </button>
                  </div>

                  {st && st.tools.length > 0 && (
                    <button
                      onClick={() => toggleTools(name)}
                      className="inline-flex items-center gap-1 mt-3 text-[11px] font-medium text-base-500 hover:text-accent transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20 rounded-lg px-1 -ml-1"
                      aria-expanded={isExpanded}
                    >
                      <ChevronDown size={12} className={`transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                      {st.tools.length} tool{st.tools.length > 1 ? 's' : ''} · {isExpanded ? 'hide' : 'show'}
                      <Wrench size={11} className="ml-1 opacity-60" />
                    </button>
                  )}
                </div>

                {isExpanded && st && st.tools.length > 0 && (
                  <div className="px-4 pb-3 space-y-1 border-t border-base-700/30 pt-3 bg-base-900/20">
                    {st.tools.map((t) => (
                      <div key={t.name} className="flex items-start justify-between gap-3 px-3 py-2 rounded-xl bg-base-900/60 border border-base-700/50">
                        <div className="min-w-0">
                          <p className="text-[11px] text-base-200 font-mono truncate">{t.name}</p>
                          {t.description && <p className="text-[11px] leading-relaxed text-base-500 line-clamp-1">{t.description}</p>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {entries.length > 0 && (
        <button
          onClick={openCatalog}
          className="w-full inline-flex items-center justify-center gap-2 py-3 rounded-2xl border-2 border-dashed border-base-700 hover:border-accent/30 text-base-400 hover:text-accent text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20"
        >
          <PackagePlus size={16} /> Install more connectors
        </button>
      )}

      {/* ── Store modal ── */}
      {catalogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setCatalogOpen(false)} />
          <div className="relative w-full max-w-3xl max-h-[85vh] rounded-2xl border border-base-700 bg-base-900 shadow-panel flex flex-col overflow-hidden animate-fadeIn">
            <div className="flex items-center gap-3 px-5 py-4 border-b border-base-700/60 shrink-0">
              <div className="w-9 h-9 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center shrink-0">
                <Store size={16} className="text-accent" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold tracking-tight text-base-100">Connector Store</p>
                <p className="text-xs text-base-500">Curated MCP servers you can install in one click</p>
              </div>
              <div className="relative hidden sm:block w-56 shrink-0">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-base-500" />
                <input
                  value={catalogSearch}
                  onChange={(e) => setCatalogSearch(e.target.value)}
                  placeholder="Search store…"
                  className="w-full bg-base-800 border border-base-700 rounded-xl pl-9 pr-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15"
                  autoFocus
                />
              </div>
              <button
                onClick={() => {
                  setCatalogOpen(false)
                  setCatalogSearch('')
                }}
                className="p-2 rounded-xl text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20"
                aria-label="Close store"
              >
                <X size={16} />
              </button>
            </div>

            <div className="sm:hidden px-4 py-3 border-b border-base-700/40">
              <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-base-500" />
                <input
                  value={catalogSearch}
                  onChange={(e) => setCatalogSearch(e.target.value)}
                  placeholder="Search connectors…"
                  className="w-full bg-base-800 border border-base-700 rounded-xl pl-9 pr-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4 sm:p-5">
              {filteredCatalog.length === 0 ? (
                <div className="py-12 text-center">
                  <p className="text-sm text-base-300">No connectors match your search</p>
                  <p className="text-xs text-base-500 mt-1">Try “filesystem”, “github”, or “browser”.</p>
                </div>
              ) : (
                <div className="space-y-6">
                  {Object.entries(catalogGroups).map(([category, list]) => (
                    <div key={category}>
                      <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-3">{category}</p>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {list.map((e) => {
                          const needsToken = e.env_vars.some((ev) => !ev.optional)
                          const needsNode = e.command === 'npx'
                          return (
                            <div
                              key={e.id}
                              className="flex flex-col p-4 rounded-2xl border border-base-700/60 bg-base-800/30 hover:border-accent/25 hover:bg-base-800/60 transition-colors group"
                            >
                              <div className="flex items-start gap-3 mb-2">
                                <div className="w-9 h-9 rounded-xl bg-accent/10 border border-accent/15 flex items-center justify-center shrink-0">
                                  <Puzzle size={16} className="text-accent" />
                                </div>
                                <div className="flex-1 min-w-0">
                                  <p className="text-[13px] font-semibold text-base-100 leading-tight">{e.display_name}</p>
                                  <p className="text-[11px] leading-relaxed text-base-500 line-clamp-2 mt-0.5">{e.description}</p>
                                </div>
                              </div>
                              <div className="flex flex-wrap gap-1 mb-2">
                                {e.capabilities.map((c) => {
                                  const cd = CAPABILITY_DEFS[c]
                                  if (!cd) return null
                                  return <CapabilityBadge key={c} icon={cd.icon} label={cd.label} />
                                })}
                              </div>
                              <div className="flex flex-wrap gap-1.5 mb-3">
                                {needsNode && (
                                  <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-amber-500/10 text-[10px] font-medium text-amber-300 border border-amber-500/20">Node required</span>
                                )}
                                {needsToken && (
                                  <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-red-500/10 text-[10px] font-medium text-red-300 border border-red-500/20">Token required</span>
                                )}
                                {!needsNode && !needsToken && (
                                  <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-emerald-500/10 text-[10px] font-medium text-emerald-300 border border-emerald-500/20">Ready to install</span>
                                )}
                              </div>
                              <button
                                onClick={() => pickCatalog(e)}
                                className="w-full mt-auto py-2 rounded-xl text-xs font-medium bg-accent hover:bg-accent/90 text-white transition-colors focus:outline-none focus:ring-2 focus:ring-accent/30"
                              >
                                Install
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Add manually modal ── */}
      {addOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => { setAddOpen(false); clearForm() }} />
          <div className="relative w-full max-w-lg rounded-2xl border border-base-700 bg-base-900 shadow-panel overflow-hidden animate-fadeIn max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b border-base-700/60 shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center">
                  <Plug size={16} className="text-accent" />
                </div>
                <div>
                  <p className="text-sm font-semibold tracking-tight text-base-100">Add connector</p>
                  <p className="text-xs text-base-500">Manual MCP server</p>
                </div>
              </div>
              <button
                onClick={() => { setAddOpen(false); clearForm() }}
                className="p-2 rounded-xl text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors"
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {selectedCatalog && (
                <div className="px-3 py-2 rounded-xl bg-accent/10 border border-accent/20 text-[11px] text-accent">
                  Pre-filled from <span className="font-semibold">{selectedCatalog.display_name}</span> — check the details before adding.
                </div>
              )}

              <label className="block">
                <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">
                  Server name <span className="text-red-400">*</span>
                </span>
                <input
                  value={addName}
                  onChange={(e) => setAddName(e.target.value)}
                  placeholder="e.g. Filesystem"
                  className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 transition-colors"
                />
              </label>

              <label className="block">
                <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">
                  Command <span className="text-red-400">*</span>
                </span>
                <input
                  value={addCommand}
                  onChange={(e) => setAddCommand(e.target.value)}
                  placeholder="e.g. npx"
                  className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 font-mono transition-colors"
                />
              </label>

              <label className="block">
                <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">Arguments</span>
                <input
                  value={addArgs}
                  onChange={(e) => setAddArgs(e.target.value)}
                  placeholder="Comma-separated, e.g. -y, @modelcontextprotocol/server-filesystem"
                  className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 font-mono transition-colors"
                />
                <span className="block text-[10px] text-base-500 mt-1">Separate each argument with a comma</span>
              </label>

              {selectedCatalog && selectedCatalog.env_vars.length > 0 ? (
                <div className="space-y-3 p-3 rounded-xl bg-base-800/40 border border-base-700/40">
                  <p className="text-[10px] font-semibold tracking-wider uppercase text-base-400">Environment variables</p>
                  {selectedCatalog.env_vars.map((ev) => (
                    <label key={ev.key} className="block">
                      <span className="block text-[11px] text-base-300 mb-1">
                        {ev.label} {ev.secret && <span className="text-base-500 text-[10px]">· secret</span>} {!ev.optional && <span className="text-red-400">*</span>}
                      </span>
                      <input
                        value={catalogEnvVars[ev.key] ?? ''}
                        onChange={(e) => setCatalogEnvVars({ ...catalogEnvVars, [ev.key]: e.target.value })}
                        placeholder={ev.optional ? `(optional) ${ev.key}` : ev.key}
                        type={ev.secret ? 'password' : 'text'}
                        className="w-full bg-base-900 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 font-mono"
                      />
                    </label>
                  ))}
                </div>
              ) : (
                <label className="block">
                  <span className="block text-[10px] font-medium tracking-wider uppercase text-base-400 mb-1.5">Environment variables</span>
                  <input
                    value={addEnv}
                    onChange={(e) => setAddEnv(e.target.value)}
                    placeholder="Comma-separated, e.g. KEY=value, FOO=bar"
                    className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/15 font-mono transition-colors"
                  />
                </label>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-base-700/40 bg-base-900/50 shrink-0">
              <button onClick={() => { setAddOpen(false); clearForm() }} className="px-4 py-2 rounded-xl text-xs font-medium text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20">
                Cancel
              </button>
              <button
                onClick={handleAdd}
                disabled={!addName.trim() || !addCommand.trim()}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-accent text-white hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-accent/30"
              >
                Add connector
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Detail drawer ── */}
      {detailName && serverDetail && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={closeDetail} />
          <div className="relative w-[26rem] max-w-full h-full bg-base-900 border-l border-base-700 overflow-y-auto shadow-panel animate-fadeIn">
            <div className="p-5 space-y-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <h2 className="text-[15px] font-semibold tracking-tight text-base-100">{serverDetail.name}</h2>
                    <span
                      className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
                        serverDetail.status === 'ok'
                          ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20'
                          : serverDetail.status === 'error'
                            ? 'text-red-300 bg-red-500/10 border-red-500/20'
                            : 'text-base-400 bg-base-800 border-base-600'
                      }`}
                    >
                      {serverDetail.status === 'ok' ? 'Connected' : serverDetail.status === 'error' ? 'Error' : 'Disconnected'}
                    </span>
                  </div>
                  {serverDetail.description && <p className="text-xs leading-relaxed text-base-500">{serverDetail.description}</p>}
                </div>
                <button onClick={closeDetail} className="p-2 rounded-xl text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors shrink-0 focus:outline-none focus:ring-2 focus:ring-accent/20" aria-label="Close details">
                  <X size={16} />
                </button>
              </div>

              {serverDetail.capabilities.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-2">Capabilities</p>
                  <div className="flex flex-wrap gap-1.5">
                    {serverDetail.capabilities.map((c: string) => {
                      const cd = CAPABILITY_DEFS[c]
                      if (!cd) return null
                      return <CapabilityBadge key={c} icon={cd.icon} label={cd.label} size="md" />
                    })}
                  </div>
                </div>
              )}

              {serverDetail.tools.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-2">
                    {serverDetail.tools.length} tool{serverDetail.tools.length > 1 ? 's' : ''}
                  </p>
                  <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
                    {serverDetail.tools.map((t: McpServerTool) => (
                      <div key={t.name} className="px-3 py-2 rounded-xl bg-base-800/50 border border-base-700/50">
                        <p className="text-[11px] text-base-200 font-mono truncate">{t.name}</p>
                        {t.description && <p className="text-[11px] leading-relaxed text-base-500 line-clamp-1">{t.description}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {serverDetail.capabilities.length > 0 && devMode && (
                <div>
                  <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-2">Permissions</p>
                  <div className="space-y-1">
                    {(
                      serverDetail.capabilities
                        .flatMap((c: string) => PERMISSION_DEFS[c] ?? PERMISSION_DEFS._default)
                        .filter((p, i, a) => a.findIndex((x) => x.key === p.key) === i) as { key: string; label: string }[]
                    ).map((perm) => {
                      const currentPerms = servers[serverDetail.name]?.permissions ?? {}
                      const checked = currentPerms[perm.key] !== false
                      return (
                        <label
                          key={perm.key}
                          className="flex items-center gap-2 px-3 py-2 rounded-xl bg-base-800/30 border border-base-700/50 cursor-pointer hover:bg-base-800/50 transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => setPermission(serverDetail.name, perm.key, !checked)}
                            className="accent-accent w-3.5 h-3.5 rounded"
                          />
                          <span className="text-[11px] text-base-300">{perm.label}</span>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}

              {serverDetail.config && Object.keys(serverDetail.config.env ?? {}).length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-2">Configuration</p>
                  <div className="space-y-2">
                    {Object.entries(serverDetail.config.env ?? {}).map(([key, val]) => (
                      <div key={key}>
                        <label className="block text-[10px] font-medium tracking-wider uppercase text-base-500 mb-1">{key}</label>
                        <input value={typeof val === 'string' ? val : '••••••••'} readOnly type="password" className="w-full bg-base-800 border border-base-700 rounded-xl px-3 py-2.5 text-xs text-base-200 font-mono outline-none" />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {serverDetail.diagnostics && (
                <div>
                  <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-2">Diagnostics</p>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { label: 'Transport', value: serverDetail.diagnostics.transport },
                      {
                        label: 'Response time',
                        value: serverDetail.diagnostics.response_time_ms != null ? `${serverDetail.diagnostics.response_time_ms}ms` : '—',
                      },
                      {
                        label: 'Started',
                        value: serverDetail.diagnostics.startup_time_ms != null ? formatTimeAgo(serverDetail.diagnostics.startup_time_ms) : '—',
                      },
                      {
                        label: 'Status',
                        value: serverDetail.status === 'ok' ? 'Healthy' : serverDetail.status === 'error' ? 'Error' : 'Offline',
                      },
                    ].map((d) => (
                      <div key={d.label} className="px-3 py-2.5 rounded-xl bg-base-800/30 border border-base-700/50">
                        <p className="text-[9px] font-medium tracking-wider uppercase text-base-500">{d.label}</p>
                        <p className="text-xs text-base-200 font-medium mt-0.5">{d.value}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {devMode && (
                <div>
                  <p className="text-[10px] font-semibold tracking-widest uppercase text-base-500 mb-2">Advanced</p>
                  <div className="space-y-2">
                    <div className="px-3 py-2.5 rounded-xl bg-base-800/30 border border-base-700/50">
                      <p className="text-[9px] font-medium tracking-wider uppercase text-base-500">Command</p>
                      <p className="text-[11px] text-base-200 font-mono mt-0.5">{serverDetail.config?.command ?? '—'}</p>
                    </div>
                    {serverDetail.config?.args && serverDetail.config.args.length > 0 && (
                      <div className="px-3 py-2.5 rounded-xl bg-base-800/30 border border-base-700/50">
                        <p className="text-[9px] font-medium tracking-wider uppercase text-base-500">Args</p>
                        <p className="text-[11px] text-base-200 font-mono mt-0.5 break-all">{serverDetail.config.args.join(' ')}</p>
                      </div>
                    )}
                    <div className="px-3 py-2.5 rounded-xl bg-base-800/30 border border-base-700/50">
                      <p className="text-[9px] font-medium tracking-wider uppercase text-base-500">Transport</p>
                      <p className="text-[11px] text-base-200 font-mono mt-0.5">{serverDetail.diagnostics?.transport ?? 'stdio'}</p>
                    </div>
                    <details className="group">
                      <summary className="text-[11px] font-medium text-base-500 cursor-pointer hover:text-base-300 transition-colors select-none">Raw config JSON</summary>
                      <pre className="mt-2 p-3 rounded-xl bg-base-950 border border-base-700/50 text-[10px] text-base-400 font-mono overflow-x-auto max-h-40 overflow-y-auto">
                        {JSON.stringify(serverDetail.config, null, 2)}
                      </pre>
                    </details>
                    <details className="group">
                      <summary className="text-[11px] font-medium text-base-500 cursor-pointer hover:text-base-300 transition-colors select-none">Raw diagnostics</summary>
                      <pre className="mt-2 p-3 rounded-xl bg-base-950 border border-base-700/50 text-[10px] text-base-400 font-mono overflow-x-auto max-h-40 overflow-y-auto">
                        {JSON.stringify(serverDetail.diagnostics, null, 2)}
                      </pre>
                    </details>
                  </div>
                </div>
              )}

              <div className="flex items-center gap-2 pt-2 border-t border-base-700/30">
                <button
                  onClick={async () => {
                    const removed = await handleDelete(serverDetail.name)
                    if (removed) closeDetail()
                  }}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium text-red-300 hover:text-red-200 hover:bg-red-500/10 border border-red-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-err/20"
                >
                  <Trash2 size={13} /> Remove
                </button>
                <button
                  onClick={() => handleTest(serverDetail.name)}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium text-base-300 hover:text-base-100 hover:bg-base-800 border border-base-700 transition-colors focus:outline-none focus:ring-2 focus:ring-accent/20"
                >
                  <Power size={13} /> Test
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
