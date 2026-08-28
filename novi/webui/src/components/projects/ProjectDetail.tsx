import { useState, useEffect } from 'react'
import { ArrowLeft, ExternalLink, Edit3, Save, X, Plus, Folder, HardDrive } from 'lucide-react'
import { Project, Conversation } from '@/types'
import { API_BASE } from '@/components/settings/api'

interface Props {
  project: Project
  conversations: Conversation[]
  onBack: () => void
  onUpdate: (id: string, data: Partial<Project>) => void
  onSelectConversation: (id: string) => void
  onRemoveConversation: (convId: string, projId: string) => void
  onStartConversation?: () => void
}

export function ProjectDetail({ project, conversations, onBack, onUpdate, onSelectConversation, onRemoveConversation, onStartConversation }: Props) {
  const [editingContext, setEditingContext] = useState(false)
  const [contextValue, setContextValue] = useState(project.sharedContext)
  const [workspacePath, setWorkspacePath] = useState(project.workspace?.root ?? "")
  const [workspaceBusy, setWorkspaceBusy] = useState(false)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)

  useEffect(() => { setWorkspacePath(project.workspace?.root ?? "") }, [project.workspace?.root])
  useEffect(() => { setContextValue(project.sharedContext) }, [project.sharedContext])

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
      else onUpdate(project.id, { workspace: data.workspace } as any)
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

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-base-950">
      <header className="h-14 shrink-0 flex items-center gap-3 px-5 border-b border-base-800">
        <button onClick={onBack} className="p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors">
          <ArrowLeft size={17} />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-medium text-base-100 truncate">{project.name}</h2>
          {project.description && (
            <p className="text-[11px] text-base-500 truncate">{project.description}</p>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-base-400">Shared Context</h3>
            {!editingContext ? (
              <button onClick={() => setEditingContext(true)} className="p-1 rounded text-base-400 hover:text-base-100">
                <Edit3 size={13} />
              </button>
            ) : (
              <div className="flex gap-1">
                <button onClick={saveContext} className="p-1 rounded text-accent hover:text-accent/80">
                  <Save size={13} />
                </button>
                <button onClick={() => setEditingContext(false)} className="p-1 rounded text-base-400 hover:text-base-100">
                  <X size={13} />
                </button>
              </div>
            )}
          </div>
          {editingContext ? (
            <textarea
              value={contextValue}
              onChange={(e) => setContextValue(e.target.value)}
              rows={6}
              className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent resize-none"
            />
          ) : (
            <div className="bg-base-850 rounded-lg px-3 py-3 text-sm text-base-300 whitespace-pre-wrap min-h-[60px]">
              {project.sharedContext || <span className="text-base-500 italic">No shared context set.</span>}
            </div>
          )}
        </section>

        <section className="rounded-xl border border-base-800 bg-base-900/30 p-4">
          <div className="flex items-center gap-2 mb-2">
            <HardDrive size={14} className="text-accent" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-base-300">Workspace</h3>
            <span className="text-[11px] text-base-500">READ only for beta — local folder Novi can inspect</span>
          </div>
          {project.workspace?.root ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs text-base-300 bg-base-800 rounded-lg px-3 py-2 border border-base-700/50">
                <Folder size={13} className="text-base-500 shrink-0" />
                <span className="truncate flex-1 font-mono">{project.workspace.root}</span>
                <span className="text-[11px] text-base-500">READ</span>
              </div>
              <div className="flex items-center gap-2 text-[11px] text-base-500">
                <span>{project.workspace.stats?.total ?? '—'} files indexed</span>
                <span>·</span>
                <span>{project.workspace.indexedAt ? new Date(project.workspace.indexedAt).toLocaleString() : '—'}</span>
              </div>
              <div className="flex gap-2">
                <input value={workspacePath} onChange={(e) => setWorkspacePath(e.target.value)} placeholder="Local folder path" className="flex-1 bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 font-mono" />
                <button onClick={pickFolder} className="px-3 py-2 rounded-lg border border-base-700 bg-base-800 text-xs text-base-300 hover:bg-base-700 transition-colors">Browse</button>
                <button onClick={attachWorkspace} disabled={workspaceBusy} className="px-3 py-2 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium disabled:opacity-40 transition-colors">{workspaceBusy ? 'Attaching…' : 'Update'}</button>
              </div>
              {workspaceError && <p className="text-xs text-err">{workspaceError}</p>}
              <p className="text-[11px] text-base-500">Novi can search and read files here. It will not modify files without your confirmation. Excludes .git, node_modules, venv, build, etc.</p>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-base-500">Attach a local folder so Novi can inspect it — it will be able to answer "Find where model routing is implemented" and similar.</p>
              <div className="flex gap-2">
                <input value={workspacePath} onChange={(e) => setWorkspacePath(e.target.value)} placeholder="e.g. D:\Projects\MyApp or /home/user/project" className="flex-1 bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 font-mono" />
                <button onClick={pickFolder} className="px-3 py-2 rounded-lg border border-base-700 bg-base-800 text-xs text-base-300 hover:bg-base-700 transition-colors">Browse</button>
                <button onClick={attachWorkspace} disabled={!workspacePath.trim() || workspaceBusy} className="px-4 py-2 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium disabled:opacity-40 transition-colors">{workspaceBusy ? 'Attaching…' : 'Attach'}</button>
              </div>
              {workspaceError && <p className="text-xs text-err">{workspaceError}</p>}
              <p className="text-[11px] text-base-500">READ only — Novi can list, search, and read files. Write and execute remain disabled for beta.</p>
            </div>
          )}
        </section>

        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-base-400">
              Conversations ({project.conversationIds.length})
            </h3>
            {onStartConversation && (
              <button onClick={onStartConversation} className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-accent/20">
                <Plus size={12} /> New chat
              </button>
            )}
          </div>
          {project.conversationIds.length === 0 ? (
            <p className="text-sm text-base-500 italic">No conversations linked to this project yet — start one to work in this project’s context.</p>
          ) : (
            <div className="space-y-1">
              {project.conversationIds.map(cid => {
                const conv = conversations.find(c => c.id === cid)
                return (
                  <div key={cid} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-base-850 hover:bg-base-800 transition-colors group">
                    <button
                      onClick={() => onSelectConversation(cid)}
                      className="flex-1 flex items-center gap-2 text-sm text-base-300 hover:text-base-100 text-left min-w-0"
                    >
                      <ExternalLink size={13} className="shrink-0" />
                      <span className="truncate">{conv?.title ?? cid}</span>
                    </button>
                    <button
                      onClick={() => onRemoveConversation(cid, project.id)}
                      className="shrink-0 opacity-0 group-hover:opacity-100 p-0.5 rounded text-base-400 hover:text-err transition-all"
                      title="Remove from project"
                    >
                      <X size={13} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
