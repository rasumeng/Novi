import { useState, useEffect, useMemo } from 'react'
import { X, Search, Store, Plug, PackagePlus, Settings, Power, Trash2, ChevronDown, Puzzle } from 'lucide-react'
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
      description: "Novi will lose access to any tools this connector provides. You can add it again later.",
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

  return (
    <div className="space-y-4">
      {dialog}
      <div className="flex items-center justify-between">
        <p className="text-xs text-base-500">Connect Novi to external tools — databases, APIs, file systems, and more.</p>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={openCatalog} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-accent/40 text-accent hover:bg-accent/10 text-xs font-medium transition-colors">
            <Store size={14} /> Browse
          </button>
          <button onClick={() => { clearForm(); setAddOpen(true) }} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors">
            <Plug size={14} /> Add
          </button>
        </div>
      </div>

      {entries.length > 0 && (
        <div className="p-4 rounded-xl border border-base-700/60 bg-base-900/30">
          <div className="flex items-center gap-2 mb-3">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-base-400">Capabilities</p>
            <span className="text-[10px] text-base-500">({activeCapabilities.size} enabled)</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(CAPABILITY_DEFS).map(([key, def]) => {
              const enabled = activeCapabilities.has(key)
              const Icon = def.icon
              return (
                <span
                  key={key}
                  className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium transition-colors ${
                    enabled
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'bg-base-800 text-base-500 border border-base-700/50'
                  }`}
                >
                  <Icon size={12} /> {def.label}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {catalogOpen && (
        <div className="p-4 rounded-xl border border-base-700 bg-base-900">
          <div className="flex items-center gap-2 mb-4">
            <Store size={16} className="text-accent shrink-0" />
            <p className="text-sm font-semibold text-base-100">Connector Store</p>
            <div className="flex-1" />
            <div className="relative max-w-60">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-500" />
              <input value={catalogSearch} onChange={(e) => setCatalogSearch(e.target.value)} placeholder="Search connectors..." className="w-full bg-base-800 border border-base-700 rounded-lg pl-7 pr-3 py-1.5 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40" autoFocus />
            </div>
            <button onClick={() => { setCatalogOpen(false); setCatalogSearch('') }} className="p-1.5 rounded-lg text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors"><X size={15} /></button>
          </div>

          {filteredCatalog.length === 0 ? (
            <p className="text-xs text-base-500 text-center py-6">No connectors match your search</p>
          ) : (
            <div className="space-y-5 max-h-96 overflow-y-auto pr-1">
              {Object.entries(catalogGroups).map(([category, entries]) => (
                <div key={category}>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2.5">{category}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {entries.map((e) => {
                      const needsToken = e.env_vars.some(ev => !ev.optional)
                      const needsNode = e.command === 'npx'
                      return (
                        <div key={e.id} className="flex flex-col p-3.5 rounded-xl border border-base-700/60 bg-base-800/40 hover:border-accent/30 hover:bg-base-800/70 transition-colors group">
                          <div className="flex items-start gap-2.5 mb-2">
                            <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
                              <Puzzle size={16} className="text-accent" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm text-base-100 font-medium">{e.display_name}</p>
                              <p className="text-[11px] text-base-500 line-clamp-2">{e.description}</p>
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-1 mb-2.5">
                            {e.capabilities.map((c) => {
                              const cd = CAPABILITY_DEFS[c]
                              if (!cd) return null
                              return <CapabilityBadge key={c} icon={cd.icon} label={cd.label} />
                            })}
                          </div>
                          <div className="flex flex-wrap gap-1.5 mb-3">
                            {needsNode && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/10 text-[9px] text-amber-400 border border-amber-500/20">
                                Node
                              </span>
                            )}
                            {needsToken && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 text-[9px] text-red-400 border border-red-500/20">
                                Token
                              </span>
                            )}
                            {!needsNode && !needsToken && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/10 text-[9px] text-emerald-400 border border-emerald-500/20">
                                Ready
                              </span>
                            )}
                          </div>
                          <button
                            onClick={() => pickCatalog(e)}
                            className="w-full py-1.5 rounded-lg text-xs font-medium bg-accent/80 hover:bg-accent text-white transition-colors mt-auto opacity-0 group-hover:opacity-100 focus:opacity-100"
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
      )}

      {addOpen && (
        <div className="p-4 rounded-xl border border-base-700 bg-base-900 space-y-3">
          {selectedCatalog && <p className="text-[11px] text-accent font-medium">Pre-filled from catalog: {selectedCatalog.display_name}</p>}
          <input value={addName} onChange={(e) => setAddName(e.target.value)} placeholder="Server name (e.g. Filesystem)" className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40" />
          <input value={addCommand} onChange={(e) => setAddCommand(e.target.value)} placeholder="Command (e.g. npx)" className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 font-mono" />
          <input value={addArgs} onChange={(e) => setAddArgs(e.target.value)} placeholder="Args (comma-separated, e.g. -y, @modelcontextprotocol/server-filesystem)" className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 font-mono" />
          {selectedCatalog && selectedCatalog.env_vars.length > 0 && (
            <div className="space-y-2">
              <p className="text-[11px] font-medium text-base-400">Environment variables</p>
              {selectedCatalog.env_vars.map((ev) => (
                <div key={ev.key}>
                  <label className="block text-[10px] text-base-500 mb-0.5">{ev.label}</label>
                  <input value={catalogEnvVars[ev.key] ?? ''} onChange={(e) => setCatalogEnvVars({ ...catalogEnvVars, [ev.key]: e.target.value })} placeholder={ev.optional ? `(optional) ${ev.key}` : ev.key} type={ev.secret ? 'password' : 'text'} className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 font-mono" />
                </div>
              ))}
            </div>
          )}
          {!selectedCatalog && <input value={addEnv} onChange={(e) => setAddEnv(e.target.value)} placeholder="Env vars (comma-separated, e.g. KEY=value, FOO=bar)" className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 font-mono" />}
          <div className="flex items-center gap-2 justify-end">
            <button onClick={() => { setAddOpen(false); clearForm() }} className="px-3 py-1.5 rounded-lg text-xs text-base-400 hover:text-base-200 transition-colors">Cancel</button>
            <button onClick={handleAdd} disabled={!addName.trim() || !addCommand.trim()} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors">Add server</button>
          </div>
        </div>
      )}

      {testResult && (
        <div className={`px-3 py-2 rounded-lg text-xs ${testResult.startsWith('Connected') ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30'}`}>
          {testResult}
        </div>
      )}

      {entries.length === 0 && !addOpen && !catalogOpen ? (
        <div className="flex flex-col items-center justify-center h-32 rounded-xl border-2 border-dashed border-base-700 text-base-500 text-sm">
          No connectors added yet
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map(([name, cfg]) => {
            const st = serverStatus?.[name]
            const caps = serverCapabilities[name] ?? []
            const entry = catalogByName[name]
            const desc = entry?.description ?? `${cfg.command}${cfg.args ? ' ' + cfg.args.join(' ') : ''}`
            const needsToken = entry && entry.env_vars.some(ev => !ev.optional) && (!cfg.env || Object.keys(cfg.env).length === 0)
            return (
              <div key={name} className="rounded-xl bg-base-800/40 border border-base-700 overflow-hidden group">
                <div className="px-4 py-3.5">
                  <div className="flex items-start justify-between mb-1">
                    <div className="flex items-center gap-2.5 min-w-0">
                      {st?.status === 'ok' ? (
                        <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 shrink-0 mt-0.5" title="Connected" />
                      ) : st?.status === 'error' ? (
                        <span className="w-2.5 h-2.5 rounded-full bg-red-400 shrink-0 mt-0.5" title="Error" />
                      ) : (
                        <span className="w-2.5 h-2.5 rounded-full bg-base-600 shrink-0 mt-0.5" title="Disconnected" />
                      )}
                      <p className="text-sm text-base-100 font-semibold truncate">{name}</p>
                    </div>
                    {needsToken ? (
                      <span className="shrink-0 text-[10px] font-medium text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded-full border border-amber-500/20">Needs Token</span>
                    ) : st?.status === 'ok' ? (
                      <span className="shrink-0 text-[10px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">Connected</span>
                    ) : st?.status === 'error' ? (
                      <span className="shrink-0 text-[10px] font-medium text-red-400 bg-red-500/10 px-2 py-0.5 rounded-full border border-red-500/20">Error</span>
                    ) : (
                      <span className="shrink-0 text-[10px] font-medium text-base-500 bg-base-800 px-2 py-0.5 rounded-full border border-base-600">Disconnected</span>
                    )}
                  </div>
                  <p className="text-[11px] text-base-500 line-clamp-1 ml-[22px] mb-1.5">{desc}</p>
                  <div className="flex items-center gap-1.5 flex-wrap ml-[22px]">
                    {caps.map((c) => {
                      const cd = CAPABILITY_DEFS[c]
                      if (!cd) return null
                      return <CapabilityBadge key={c} icon={cd.icon} label={cd.label} />
                    })}
                    {st && st.tools.length > 0 && (
                      <button onClick={() => toggleTools(name)} className="flex items-center gap-0.5 text-[10px] text-base-500 hover:text-accent transition-colors ml-auto">
                        <ChevronDown size={11} className={`transition-transform ${expandedTools[name] ? 'rotate-180' : ''}`} />
                        {st.tools.length} tool{st.tools.length > 1 ? 's' : ''}
                      </button>
                    )}
                  </div>
                </div>
                <div className="flex items-center justify-end gap-1 px-4 py-1.5 bg-base-900/30 border-t border-base-700/40 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button onClick={() => openDetail(name)} className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-base-400 hover:text-accent hover:bg-base-800 transition-colors">
                    <Settings size={11} /> Configure
                  </button>
                  <button onClick={() => handleTest(name)} className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-base-400 hover:text-accent hover:bg-base-800 transition-colors">
                    <Power size={11} /> Test
                  </button>
                  <button onClick={() => handleDelete(name)} className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-base-400 hover:text-err hover:bg-base-800 transition-colors">
                    <Trash2 size={11} /> Remove
                  </button>
                </div>
                {expandedTools[name] && st && st.tools.length > 0 && (
                  <div className="px-4 pb-3 space-y-0.5">
                    {st.tools.map((t) => (
                      <div key={t.name} className="px-3 py-1.5 rounded-lg bg-base-900/50 border border-base-700/50">
                        <p className="text-[11px] text-base-200 font-mono">{t.name}</p>
                        {t.description && <p className="text-[10px] text-base-500 line-clamp-1">{t.description}</p>}
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
        <button onClick={openCatalog} className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border-2 border-dashed border-base-700 hover:border-accent/40 text-base-500 hover:text-accent text-xs font-medium transition-colors">
          <PackagePlus size={16} /> Install More Connectors
        </button>
      )}

      {detailName && serverDetail && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/40" onClick={closeDetail} />
          <div className="relative w-[26rem] max-w-full h-full bg-base-900 border-l border-base-700 overflow-y-auto">
            <div className="p-5 space-y-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <h2 className="text-base font-semibold text-base-100">{serverDetail.name}</h2>
                    <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                      serverDetail.status === 'ok' ? 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/20' :
                      serverDetail.status === 'error' ? 'text-red-400 bg-red-500/10 border border-red-500/20' :
                      'text-base-400 bg-base-800 border border-base-600'
                    }`}>
                      {serverDetail.status === 'ok' ? 'Connected' :
                       serverDetail.status === 'error' ? 'Error' :
                       'Disconnected'}
                    </span>
                  </div>
                  {serverDetail.description && (
                    <p className="text-xs text-base-500">{serverDetail.description}</p>
                  )}
                </div>
                <button onClick={closeDetail} className="p-1.5 rounded-lg text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors shrink-0">
                  <X size={16} />
                </button>
              </div>

              {serverDetail.capabilities.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2">Capabilities</p>
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
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2">{serverDetail.tools.length} Tool{serverDetail.tools.length > 1 ? 's' : ''}</p>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {serverDetail.tools.map((t: McpServerTool) => (
                      <div key={t.name} className="px-3 py-2 rounded-lg bg-base-800/50 border border-base-700/50">
                        <p className="text-[11px] text-base-200 font-mono">{t.name}</p>
                        {t.description && <p className="text-[10px] text-base-500 line-clamp-1">{t.description}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {serverDetail.capabilities.length > 0 && devMode && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2">Permissions</p>
                  <div className="space-y-1">
                    {(serverDetail.capabilities.flatMap((c: string) => PERMISSION_DEFS[c] ?? PERMISSION_DEFS._default).filter((p, i, a) => a.findIndex((x) => x.key === p.key) === i)).map((perm) => {
                      const currentPerms = servers[serverDetail.name]?.permissions ?? {}
                      const checked = currentPerms[perm.key] !== false
                      return (
                        <label key={perm.key} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-base-800/30 border border-base-700/50 cursor-pointer hover:bg-base-800/50 transition-colors">
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
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2">Configuration</p>
                  <div className="space-y-2">
                    {(Object.entries(serverDetail.config.env ?? {})).map(([key, val]) => (
                      <div key={key}>
                        <label className="block text-[10px] text-base-500 mb-0.5">{key}</label>
                        <input value={typeof val === 'string' ? val : '••••••••'} readOnly type="password" className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 font-mono outline-none" />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {serverDetail.diagnostics && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2">Diagnostics</p>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { label: 'Transport', value: serverDetail.diagnostics.transport },
                      { label: 'Response Time', value: serverDetail.diagnostics.response_time_ms != null ? `${serverDetail.diagnostics.response_time_ms}ms` : 'N/A' },
                      { label: 'Startup', value: serverDetail.diagnostics.startup_time_ms != null ? formatTimeAgo(serverDetail.diagnostics.startup_time_ms) : 'N/A' },
                      { label: 'Status', value: serverDetail.status === 'ok' ? 'Healthy' : serverDetail.status === 'error' ? 'Error' : 'Offline' },
                    ].map((d) => (
                      <div key={d.label} className="px-3 py-2 rounded-lg bg-base-800/30 border border-base-700/50">
                        <p className="text-[9px] text-base-500">{d.label}</p>
                        <p className="text-xs text-base-200 font-medium">{d.value}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {devMode && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-2">Advanced</p>
                  <div className="space-y-2">
                    <div className="px-3 py-2 rounded-lg bg-base-800/30 border border-base-700/50">
                      <p className="text-[9px] text-base-500">Command</p>
                      <p className="text-[11px] text-base-200 font-mono">{serverDetail.config?.command ?? 'N/A'}</p>
                    </div>
                    {serverDetail.config?.args && serverDetail.config.args.length > 0 && (
                      <div className="px-3 py-2 rounded-lg bg-base-800/30 border border-base-700/50">
                        <p className="text-[9px] text-base-500">Args</p>
                        <p className="text-[11px] text-base-200 font-mono">{serverDetail.config.args.join(' ')}</p>
                      </div>
                    )}
                    <div className="px-3 py-2 rounded-lg bg-base-800/30 border border-base-700/50">
                      <p className="text-[9px] text-base-500">Transport</p>
                      <p className="text-[11px] text-base-200 font-mono">{serverDetail.diagnostics?.transport ?? 'stdio'}</p>
                    </div>
                    <details className="group">
                      <summary className="text-[10px] text-base-500 cursor-pointer hover:text-base-300 transition-colors select-none">Raw Config JSON</summary>
                      <pre className="mt-2 p-3 rounded-lg bg-base-950 border border-base-700/50 text-[10px] text-base-400 font-mono overflow-x-auto max-h-40 overflow-y-auto">{JSON.stringify(serverDetail.config, null, 2)}</pre>
                    </details>
                    <details className="group">
                      <summary className="text-[10px] text-base-500 cursor-pointer hover:text-base-300 transition-colors select-none">Raw Diagnostics Data</summary>
                      <pre className="mt-2 p-3 rounded-lg bg-base-950 border border-base-700/50 text-[10px] text-base-400 font-mono overflow-x-auto max-h-40 overflow-y-auto">{JSON.stringify(serverDetail.diagnostics, null, 2)}</pre>
                    </details>
                  </div>
                </div>
              )}

              <div className="flex items-center gap-2 pt-2">
                <button
                  onClick={async () => { const removed = await handleDelete(serverDetail.name); if (removed) closeDetail() }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-red-400 hover:text-red-300 hover:bg-red-500/10 border border-red-500/30 transition-colors"
                >
                  <Trash2 size={13} /> Remove Connector
                </button>
                <button onClick={() => handleTest(serverDetail.name)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-base-400 hover:text-base-200 hover:bg-base-800 border border-base-700 transition-colors">
                  <Power size={13} /> Test Connection
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
