// ProjectDetail.tsx — hub-only view: composer + uniform chat list, no inline messages
import { useState, useEffect, useMemo, useRef } from 'react'
import { ArrowLeft, Edit3, Check, X, Plus, Folder, HardDrive, Send, Sparkles, MessageSquareText, MoveRight, Copy, Trash2 } from 'lucide-react'
import { Project, Conversation } from '@/types'
import { API_BASE } from '@/components/settings/api'
import { NoviMascot } from '@/components/brand/NoviMascot'

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
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { setWorkspacePath(project.workspace?.root ?? "") }, [project.workspace?.root])
  useEffect(() => { setContextValue(project.sharedContext) }, [project.sharedContext])

  // Authoritative: projectId field wins, fallback to legacy conversationIds
  const projectConvos = useMemo(() => {
    const ids = new Set(project.conversationIds)
    const list = conversations.filter(c => (c as any).projectId === project.id || ids.has(c.id))
    return [...list].sort((a,b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
  }, [project.conversationIds, project.id, conversations])

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
      if (!r.ok) setWorkspaceError(data.error || 'Could not attach that folder')
      else {
        onUpdate(project.id, { workspace: data.workspace } as any)
        setEditingWorkspace(false)
      }
    } catch (e: any) {
      setWorkspaceError(e?.message || 'Could not attach')
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
      textareaRef.current?.focus()
    } else if (onStartConversation) {
      onStartConversation()
    }
  }

  const handleOpenChat = (id: string) => {
    if (onOpenFull) onOpenFull(id)
    else onSelectConversation(id)
  }

  const iconBtn = "p-1 rounded-lg text-base-500 hover:text-base-100 hover:bg-base-800 transition-colors"

  const composerBar = (
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
  )

  const snippetFor = (c: Conversation) => {
    const firstUser = c.messages.find(m => m.role === 'user')?.content
    if (firstUser) return firstUser.slice(0, 80)
    const any = c.messages[0]?.content
    if (any) return any.slice(0, 80)
    return 'No messages yet'
  }

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
        {onStartConversation && (
          <button onClick={onStartConversation} className="flex items-center gap-1 px-2.5 py-1.5 rounded-full bg-base-900 border border-base-700 text-xs text-base-400 hover:text-base-100 transition-colors shrink-0">
            <Plus size={12} /> New chat
          </button>
        )}
        <button
          onClick={() => setDetailsOpen(v => !v)}
          className="hidden lg:flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border border-base-700 bg-base-900 text-xs text-base-300 hover:bg-base-850 transition-colors"
        >
          <Sparkles size={12} />
          {detailsOpen ? 'Hide details' : 'Details'}
        </button>
      </header>

      <div className="flex-1 flex min-h-0">
        {/* Center hub — never shows message contents */}
        <div className="flex-1 flex flex-col min-w-0 overflow-y-auto">
          <div className="flex-1 px-6 py-8">
            <div className="max-w-2xl mx-auto w-full flex flex-col items-center">
              <div className="flex items-center gap-3 mb-6">
                <div className="shrink-0">
                  <NoviMascot size={64} expression="happy" />
                </div>
                <p className="text-[24px] font-semibold text-base-100 leading-snug">
                  Let's talk inside {project.name}.
                </p>
              </div>

              <div className="w-full mb-4">
                {composerBar}
              </div>

              <p className="text-[11px] text-base-600 text-center mb-4">
                {projectConvos.length === 0
                  ? "I'll keep this grouped here."
                  : "Pick a chat to continue, or start a new one above."}
              </p>

              {project.sharedContext && (
                <div className="max-w-md mx-auto w-full rounded-xl bg-base-900/60 border border-base-800/30 px-3 py-2 text-left mb-6">
                  <p className="text-[10px] font-medium tracking-wide uppercase text-base-600 mb-1">I'm keeping this in mind</p>
                  <p className="text-[11px] text-base-400 line-clamp-3 whitespace-pre-wrap">{project.sharedContext}</p>
                </div>
              )}

              {/* Uniform readable chat list */}
              <div className="w-full">
                {projectConvos.length === 0 ? (
                  <div className="py-8 text-center">
                    <p className="text-sm text-base-500">No chats in this project yet</p>
                    <p className="text-xs text-base-600 mt-1">Send a message above to start the first one.</p>
                  </div>
                ) : (
                  <>
                    <p className="text-[10px] font-medium tracking-wide uppercase text-base-600 mb-2 text-center">
                      Chats in this project · {projectConvos.length}
                    </p>
                    <div className="space-y-1">
                      {projectConvos.map((c) => (
                        <div
                          key={c.id}
                          className="group flex items-center gap-3 px-3 py-3 rounded-xl hover:bg-base-900/70 border border-transparent hover:border-base-800/30 transition-colors"
                        >
                          <button
                            onClick={() => handleOpenChat(c.id)}
                            className="flex-1 flex items-center gap-3 min-w-0 text-left"
                          >
                            <span className="w-8 h-8 shrink-0 rounded-lg bg-base-900 border border-base-800/30 flex items-center justify-center text-base-500">
                              <MessageSquareText size={14} />
                            </span>
                            <div className="flex-1 min-w-0">
                              <p className="text-[13px] font-medium text-base-100 truncate group-hover:text-white">{c.title || 'Untitled conversation'}</p>
                              <p className="text-xs text-base-500 truncate">{snippetFor(c)}</p>
                            </div>
                            <span className="hidden sm:block text-[11px] text-base-600 shrink-0 ml-2">{c.updatedAt || ''}</span>
                            <MoveRight size={12} className="text-base-700 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity ml-1" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); onRemoveConversation(c.id, project.id) }}
                            className="shrink-0 opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-base-600 hover:text-base-200 hover:bg-base-800 transition-all"
                            aria-label={`Remove ${c.title || 'conversation'} from project`}
                            title="Remove from project"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Right details pane — unchanged */}
        {detailsOpen && (
          <div className="hidden lg:flex w-[340px] shrink-0 flex-col border-l border-base-800/30 bg-base-900/20 overflow-y-auto">
            <div className="px-6 py-6 space-y-4">
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
                    placeholder="Anything I should keep in mind for every conversation here?"
                    className="w-full bg-base-850 border border-base-700 rounded-lg px-3 py-2 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent/30 resize-none"
                  />
                ) : (
                  <div className="rounded-lg bg-base-950/50 border border-base-800/30 px-3 py-2.5 text-sm text-base-300 whitespace-pre-wrap min-h-[56px]">
                    {project.sharedContext || <span className="text-base-500 italic text-xs">Nothing yet — add anything I should keep in mind for this project.</span>}
                  </div>
                )}
                <p className="text-[11px] text-base-600 mt-2 leading-relaxed">I bring this into every thread here automatically.</p>
              </div>

              <div className="rounded-xl border border-base-800/40 bg-base-900 p-3.5">
                <div className="flex items-center justify-between mb-2.5">
                  <h3 className="text-xs font-medium text-base-200 flex items-center gap-1.5">
                    <HardDrive size={12} className="text-base-500" /> Workspace
                  </h3>
                  <span className="text-[11px] text-base-500">read-only</span>
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
                    <p className="text-[11px] text-base-600 leading-relaxed">I can list, search, and read files here — skipping .git, node_modules, venv, build.</p>
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {!project.workspace?.root && (
                      <p className="text-xs text-base-500 leading-relaxed">Attach a local folder and I can answer things like "where is model routing implemented?"</p>
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
                This stays here — your chat stays focused on the left.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
