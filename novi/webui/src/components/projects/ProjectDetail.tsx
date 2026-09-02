import { useState, useEffect, useRef, useMemo } from 'react'
import { ArrowLeft, Edit3, Check, X, Plus, Folder, HardDrive, Send, MessageSquare, Sparkles, ExternalLink, Copy } from 'lucide-react'
import { Project, Conversation } from '@/types'
import { API_BASE } from '@/components/settings/api'
import { MessageBubble } from '@/components/chat/MessageBubble'

interface Props {
  project: Project
  conversations: Conversation[]
  onBack: () => void
  onUpdate: (id: string, data: Partial<Project>) => void
  onSelectConversation: (id: string) => void
  onRemoveConversation: (convId: string, projId: string) => void
  onStartConversation?: () => void
  onSendInProject?: (projectId: string, content: string) => void
  activeConversationId?: string | null
  connection?: string
  generating?: boolean
  onStop?: () => void
  onOpenFull?: (id: string) => void
}

export function ProjectDetail({
  project,
  conversations,
  onBack,
  onUpdate,
  onSelectConversation,
  onRemoveConversation,
  onStartConversation,
  onSendInProject,
  activeConversationId,
  connection,
  generating,
  onStop,
  onOpenFull,
}: Props) {
  const [editingContext, setEditingContext] = useState(false)
  const [contextValue, setContextValue] = useState(project.sharedContext)
  const [workspacePath, setWorkspacePath] = useState(project.workspace?.root ?? "")
  const [workspaceBusy, setWorkspaceBusy] = useState(false)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [editingWorkspace, setEditingWorkspace] = useState(false)
  const [composer, setComposer] = useState('')
  const [detailsOpen, setDetailsOpen] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { setWorkspacePath(project.workspace?.root ?? "") }, [project.workspace?.root])
  useEffect(() => { setContextValue(project.sharedContext) }, [project.sharedContext])

  const projectConvos = useMemo(() => {
    return project.conversationIds
      .map(id => conversations.find(c => c.id === id))
      .filter(Boolean) as Conversation[]
  }, [project.conversationIds, conversations])

  const activeConv = useMemo(() => {
    if (activeConversationId) return conversations.find(c => c.id === activeConversationId) ?? null
    // default to most recent in project
    if (projectConvos.length === 0) return null
    return [...projectConvos].sort((a,b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))[0] ?? null
  }, [activeConversationId, conversations, projectConvos])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [activeConv?.messages])

  const saveContext = () => {
    onUpdate(project.id, { sharedContext: contextValue })
    setEditingContext(false)
  }

  const attachWorkspace = async () => {
    if (!workspacePath.trim()) return
    setWorkspaceBusy(true)
    setWorkspaceError(null)
    try {
      const r = await fetch(`${API_BASE}/api/projects/${project.id}/workspace`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root: workspacePath.trim(), capability: 'READ' }),
      })
      const data = await r.json()
      if (!r.ok) setWorkspaceError(data.error || 'Failed to attach workspace')
      else {
        onUpdate(project.id, { workspace: data.workspace } as any)
        setEditingWorkspace(false)
      }
    } catch (e: any) {
      setWorkspaceError(e?.message || 'Failed to attach')
    } finally {
      setWorkspaceBusy(false)
    }
  }

  const pickFolder = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/directory-picker`, { method: 'POST' })
      const data = await r.json()
      if (data.path) setWorkspacePath(data.path)
    } catch {}
  }

  const handleSend = () => {
    const text = composer.trim()
    if (!text || generating) return
    if (onSendInProject) {
      onSendInProject(project.id, text)
      setComposer('')
      // focus back
      textareaRef.current?.focus()
    } else if (onStartConversation) {
      onStartConversation()
    }
  }

  const iconBtn = "p-1 rounded-lg text-base-500 hover:text-base-100 hover:bg-base-800 transition-colors"

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-base-950">
      <header className="h-11 shrink-0 flex items-center gap-3 px-4 border-b border-base-800/20">
        <button onClick={onBack} className="p-1.5 -ml-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-900 transition-colors">
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1 min-w-0 flex items-center gap-2">
          <span className="w-6 h-6 rounded-lg bg-accent/15 border border-accent/20 flex items-center justify-center text-accent shrink-0">
            <Folder size={12} />
          </span>
          <h2 className="text-sm font-medium text-base-100 truncate">{project.name}</h2>
          <span className="hidden sm:inline text-xs text-base-500 truncate">
            · {project.description || `${projectConvos.length} conversation${projectConvos.length !== 1 ? 's' : ''}`}
          </span>
        </div>
        <button
          onClick={() => setDetailsOpen(v => !v)}
          className="hidden lg:flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border border-base-700 bg-base-900 text-xs text-base-300 hover:bg-base-850 transition-colors"
        >
          <Sparkles size={12} />
          {detailsOpen ? 'Hide details' : 'Details'}
        </button>
      </header>

      <div className="flex-1 flex min-h-0">
        {/* Chat pane */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* thread selector */}
          {projectConvos.length > 0 && (
            <div className="shrink-0 px-6 py-2.5 flex items-center gap-2 overflow-x-auto border-b border-base-800/20 bg-base-900/30">
              <span className="text-[11px] text-base-500 shrink-0">Threads</span>
              <div className="flex items-center gap-1.5">
                {projectConvos.slice(0, 6).map(c => (
                  <button
                    key={c.id}
                    onClick={() => onSelectConversation(c.id)}
                    className={`px-2.5 py-1 rounded-full text-xs border transition-colors shrink-0 max-w-[160px] truncate ${
                      activeConv?.id === c.id ? 'bg-accent text-white border-accent' : 'bg-base-900 text-base-300 border-base-700 hover:bg-base-850'
                    }`}
                    title={c.title}
                  >
                    {c.title}
                  </button>
                ))}
                {projectConvos.length > 6 && (
                  <span className="text-xs text-base-600">+{projectConvos.length - 6}</span>
                )}
              </div>
              {onStartConversation && (
                <button onClick={onStartConversation} className="ml-auto flex items-center gap-1 px-2 py-1 rounded-full bg-base-900 border border-base-700 text-xs text-base-400 hover:text-base-100 transition-colors shrink-0">
                  <Plus size={12} /> New thread
                </button>
              )}
            </div>
          )}

          <div ref={scrollRef} className="flex-1 overflow-y-auto">
            {!activeConv || activeConv.messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center px-6 py-8 text-center max-w-2xl mx-auto">
                <div className="w-10 h-10 rounded-2xl bg-accent/10 border border-accent/20 flex items-center justify-center text-accent mb-3">
                  <MessageSquare size={18} />
                </div>
                <p className="text-sm font-medium text-base-100">Chat inside {project.name}</p>
                <p className="text-xs text-base-500 mt-1 max-w-sm">
                  {projectConvos.length === 0
                    ? 'No conversations yet — send a message below and it will be linked to this project automatically.'
                    : 'This thread is empty. Send a message to continue.'}
                </p>
                {project.sharedContext && (
                  <div className="mt-4 max-w-md w-full rounded-xl bg-base-900 border border-base-800/40 px-3 py-2 text-left">
                    <p className="text-[11px] uppercase tracking-widest text-base-500 mb-1">Shared context active</p>
                    <p className="text-xs text-base-400 line-clamp-3 whitespace-pre-wrap">{project.sharedContext}</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
                {activeConv.messages.map(m => (
                  <MessageBubble key={m.id} message={m} />
                ))}
                {generating && activeConv.id === activeConversationId && (
                  <div className="flex items-center gap-1.5 px-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" />
                    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.2s' }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.4s' }} />
                  </div>
                )}
                {/* quick recent list if not showing active thread? keep remove actions accessible */}
                <div className="pt-4 border-t border-base-800/20">
                  <p className="text-[11px] uppercase tracking-widest text-base-500 mb-2">All threads</p>
                  <div className="space-y-1">
                    {projectConvos.map(c => (
                      <div key={c.id} className="group flex items-center gap-2 py-1.5">
                        <button
                          onClick={() => onSelectConversation(c.id)}
                          className={`flex-1 flex items-center gap-2 text-sm text-left truncate ${activeConv?.id === c.id ? 'text-accent' : 'text-base-300 hover:text-base-100'}`}
                        >
                          <ExternalLink size={12} className="shrink-0" />
                          <span className="truncate">{c.title}</span>
                        </button>
                        {onOpenFull && (
                          <button
                            onClick={() => onOpenFull(c.id)}
                            className="opacity-0 group-hover:opacity-100 text-[11px] text-base-500 hover:text-accent px-1"
                            title="Open in chat"
                          >
                            open
                          </button>
                        )}
                        <button
                          onClick={() => onRemoveConversation(c.id, project.id)}
                          className="opacity-0 group-hover:opacity-100 p-1 rounded text-base-500 hover:text-err transition-all"
                          title="Remove from project"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="shrink-0 border-t border-base-800/20 bg-base-950/60 backdrop-blur-sm px-6 py-3">
            <div className="max-w-3xl mx-auto">
              <div className="relative flex items-end gap-2 rounded-2xl border border-base-700 bg-base-900 focus-within:border-accent/30 transition-colors">
                <textarea
                  ref={textareaRef}
                  value={composer}
                  onChange={(e) => setComposer(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      handleSend()
                    }
                  }}
                  placeholder={connection !== 'open' ? 'Connecting…' : `Message in ${project.name}…`}
                  rows={1}
                  disabled={connection !== 'open' as any}
                  className="flex-1 resize-none bg-transparent px-4 py-3 text-sm text-base-100 placeholder:text-base-500 focus:outline-none max-h-[120px] min-h-[44px]"
                  style={{ height: 'auto' }}
                  onInput={(e) => {
                    const el = e.target as HTMLTextAreaElement
                    el.style.height = 'auto'
                    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
                  }}
                />
                <button
                  onClick={generating ? onStop : handleSend}
                  disabled={!generating && !composer.trim()}
                  className={`m-1.5 p-2 rounded-full transition-colors shrink-0 ${
                    generating ? 'bg-base-700 text-base-300' : composer.trim() ? 'bg-accent text-white hover:bg-accent/90' : 'bg-base-800 text-base-500'
                  }`}
                  aria-label={generating ? 'Stop' : 'Send'}
                >
                  {generating ? <X size={14} /> : <Send size={14} />}
                </button>
              </div>
              <p className="text-[11px] text-base-600 mt-1.5 px-1">
                Chats here inherit <span className="text-base-400">Shared context</span> and <span className="text-base-400">Workspace</span> →
              </p>
            </div>
          </div>
        </div>

        {/* Right details pane */}
        {detailsOpen && (
          <div className="hidden lg:flex w-[340px] shrink-0 flex-col border-l border-base-800/30 bg-base-900/20 overflow-y-auto">
            <div className="px-6 py-6 space-y-4">
              {/* Shared context card */}
              <div className="rounded-xl border border-base-800/40 bg-base-900 p-3.5">
                <div className="flex items-center justify-between mb-2.5">
                  <h3 className="text-xs font-medium text-base-200 flex items-center gap-1.5">
                    <Sparkles size={12} className="text-accent" /> Shared context
                  </h3>
                  <div className="flex items-center gap-1">
                    {project.sharedContext && !editingContext && (
                      <button
                        onClick={() => navigator.clipboard.writeText(project.sharedContext).catch(()=>{})}
                        className={iconBtn}
                        aria-label="Copy shared context"
                        title="Copy"
                      >
                        <Copy size={12} />
                      </button>
                    )}
                    {!editingContext ? (
                      <button onClick={() => setEditingContext(true)} className={iconBtn} aria-label="Edit shared context">
                        <Edit3 size={12} />
                      </button>
                    ) : (
                      <>
                        <button onClick={saveContext} className={`${iconBtn} text-accent`} aria-label="Save">
                          <Check size={13} />
                        </button>
                        <button onClick={() => { setContextValue(project.sharedContext); setEditingContext(false) }} className={iconBtn} aria-label="Cancel">
                          <X size={13} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {editingContext ? (
                  <textarea
                    value={contextValue}
                    onChange={(e) => setContextValue(e.target.value)}
                    rows={5}
                    autoFocus
                    placeholder="Instructions injected into every conversation in this project…"
                    className="w-full bg-base-850 border border-base-700 rounded-lg px-3 py-2 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent/30 resize-none"
                  />
                ) : (
                  <div className="rounded-lg bg-base-950/50 border border-base-800/30 px-3 py-2.5 text-sm text-base-300 whitespace-pre-wrap min-h-[56px]">
                    {project.sharedContext || <span className="text-base-500 italic text-xs">No shared context. Add project-wide instructions so Novi stays on brief.</span>}
                  </div>
                )}
                <p className="text-[11px] text-base-600 mt-2 leading-relaxed">Injected at the start of every thread in this project.</p>
              </div>

              {/* Workspace card */}
              <div className="rounded-xl border border-base-800/40 bg-base-900 p-3.5">
                <div className="flex items-center justify-between mb-2.5">
                  <h3 className="text-xs font-medium text-base-200 flex items-center gap-1.5">
                    <HardDrive size={12} className="text-base-500" /> Workspace
                  </h3>
                  <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-base-800 text-base-500 border border-base-700">Read-only</span>
                </div>

                {project.workspace?.root && !editingWorkspace ? (
                  <div className="space-y-2.5">
                    <div className="rounded-lg bg-base-950/50 border border-base-800/30 px-3 py-2.5">
                      <div className="flex items-center gap-2 text-xs text-base-300">
                        <Folder size={12} className="text-base-500 shrink-0" />
                        <span className="truncate flex-1 font-mono text-[12px]">{project.workspace.root}</span>
                        <button onClick={() => setEditingWorkspace(true)} className={iconBtn} aria-label="Change workspace">
                          <Edit3 size={11} />
                        </button>
                      </div>
                      <div className="flex items-center gap-2 text-[11px] text-base-500 mt-2">
                        <span>{project.workspace.stats?.total ?? '—'} files</span>
                        <span className="text-base-700">·</span>
                        <span className="truncate">{project.workspace.indexedAt ? new Date(project.workspace.indexedAt).toLocaleDateString() : '—'}</span>
                      </div>
                    </div>
                    <p className="text-[11px] text-base-600 leading-relaxed">Novi can list, search, and read files here. Excludes .git, node_modules, venv, build.</p>
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {!project.workspace?.root && (
                      <p className="text-xs text-base-500 leading-relaxed">Attach a local folder so Novi can answer “where is model routing implemented?”</p>
                    )}
                    <div className="flex gap-1.5">
                      <input
                        value={workspacePath}
                        onChange={(e) => setWorkspacePath(e.target.value)}
                        placeholder="D:\Projects\MyApp"
                        className="flex-1 min-w-0 bg-base-850 border border-base-700 rounded-lg px-2.5 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/30 font-mono"
                      />
                      <button onClick={pickFolder} className="px-2.5 py-2 rounded-lg bg-base-850 border border-base-700 text-xs text-base-300 hover:bg-base-800 transition-colors">
                        Browse
                      </button>
                    </div>
                    <div className="flex gap-1.5">
                      <button
                        onClick={attachWorkspace}
                        disabled={!workspacePath.trim() || workspaceBusy}
                        className="flex-1 py-2 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium disabled:opacity-40 transition-colors"
                      >
                        {workspaceBusy ? 'Attaching…' : project.workspace?.root ? 'Update' : 'Attach'}
                      </button>
                      {editingWorkspace && (
                        <button onClick={() => { setEditingWorkspace(false); setWorkspaceError(null) }} className="px-2 py-2 rounded-lg bg-base-850 border border-base-700 text-base-400">
                          <X size={13} />
                        </button>
                      )}
                    </div>
                    {workspaceError && <p className="text-xs text-err">{workspaceError}</p>}
                  </div>
                )}
              </div>

              <p className="text-[11px] text-center text-base-600">
                Project details stay here — chat stays focused on the left.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
