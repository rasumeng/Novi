// ProjectsPanel.tsx
import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Trash2, Search, X, AlertTriangle, RefreshCw } from 'lucide-react'
import { Project, Conversation } from '@/types'
import { ProjectForm } from './ProjectForm'
import { ProjectDetail } from './ProjectDetail'
import { useConfirm } from '@/hooks/useConfirm'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { NoviMascot } from '@/components/brand/NoviMascot'

interface Props {
  projects: Project[]
  conversations: Conversation[]
  onCreateProject: (name: string, description?: string, sharedContext?: string) => Promise<Project | null>
  onUpdateProject: (id: string, data: Partial<Project>) => Promise<Project | null>
  onDeleteProject: (id: string) => void
  onSelectConversation: (id: string) => void
  onRemoveConversation: (convId: string, projId: string) => void
  onSelectProject: (id: string | null) => void
  onStartProjectConversation?: (projectId: string) => void
  activeProjectId?: string | null
  onSendInProject?: (projectId: string, content: string) => void
  activeConversationId?: string | null
  connection?: string
  generating?: boolean
  onStop?: () => void
  onOpenFull?: (id: string) => void
  loading?: boolean
  error?: string | null
  onRetry?: () => void
}

export function ProjectsPanel({
  projects,
  conversations,
  onCreateProject,
  onUpdateProject,
  onDeleteProject,
  onSelectConversation,
  onRemoveConversation,
  onSelectProject,
  onStartProjectConversation,
  activeProjectId,
  onSendInProject,
  activeConversationId,
  connection,
  generating,
  onStop,
  onOpenFull,
  loading = false,
  error = null,
  onRetry,
}: Props) {
  const { confirm, dialog } = useConfirm()
  const [showForm, setShowForm] = useState(false)
  const [search, setSearch] = useState('')
  // Controlled — single source of truth is activeProjectId from useNoviChat
  const selectedProjectId = activeProjectId ?? null

  const q = search.trim().toLowerCase()
  // Authoritative count: projectId OR legacy conversationIds
  const countByProject = useMemo(() => {
    const m = new Map<string, number>()
    for (const p of projects) {
      const cnt = conversations.filter(c => (c as any).projectId === p.id || p.conversationIds.includes(c.id)).length
      m.set(p.id, cnt)
    }
    return m
  }, [projects, conversations])
  const filteredProjects = useMemo(() => {
    if (!q) return projects
    return projects.filter(p => {
      if (p.name.toLowerCase().includes(q)) return true
      if (p.description?.toLowerCase().includes(q)) return true
      if (p.sharedContext?.toLowerCase().includes(q)) return true
      for (const c of conversations) {
        if ((c as any).projectId !== p.id && !p.conversationIds.includes(c.id)) continue
        if (c.title.toLowerCase().includes(q)) return true
        const first = c.messages[0]?.content.toLowerCase() ?? ''
        if (first.includes(q)) return true
      }
      return false
    })
  }, [projects, conversations, q])

  const handleDeleteProject = async (project: Project) => {
    const ok = await confirm({
      title: `Delete "${project.name}"?`,
      description: `This removes the project. Its ${project.conversationIds.length} linked conversation${project.conversationIds.length !== 1 ? 's' : ''} won't be deleted. This can't be undone.`,
      confirmLabel: 'Delete',
    })
    if (ok) onDeleteProject(project.id)
  }

  const selectedProject = selectedProjectId ? projects.find(p => p.id === selectedProjectId) ?? null : null

  const handleBack = () => {
    onSelectProject(null)
  }

  const handleSelectProject = (id: string) => {
    onSelectProject(id)
  }

  if (selectedProject) {
    return (
      <ProjectDetail
        project={selectedProject}
        conversations={conversations}
        onBack={handleBack}
        onUpdate={onUpdateProject}
        onSelectConversation={onSelectConversation}
        onRemoveConversation={onRemoveConversation}
        onStartConversation={onStartProjectConversation ? () => onStartProjectConversation(selectedProject.id) : undefined}
        onSendInProject={onSendInProject}
        activeConversationId={activeConversationId}
        connection={connection as any}
        generating={generating}
        onStop={onStop}
        onOpenFull={onOpenFull}
      />
    )
  }

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-base-950">
      {dialog}

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-2xl mx-auto">
          <header className="flex items-center justify-between gap-2 mt-10 mb-5">
            <h1 className="text-xl font-medium text-base-100">
              Projects{projects.length > 0 && <span className="text-xs font-normal text-base-500 ml-1.5">{projects.length}</span>}
            </h1>
            <button
              onClick={() => setShowForm(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
            >
              <Plus size={14} />
              New
            </button>
          </header>

          {loading && <LoadingSkeleton rows={5} compact />}
          {!loading && error && (
            <div className="rounded-xl border border-err/30 bg-err/5 px-4 py-4 text-center">
              <p className="flex items-center justify-center gap-1.5 text-sm font-medium text-err">
                <AlertTriangle size={14} /> Couldn't load your projects
              </p>
              <p className="text-xs text-base-400 mt-1">{error}</p>
              {onRetry && (
                <button onClick={onRetry} className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-base-800 border border-base-700 text-xs text-base-300 hover:bg-base-700 transition-colors">
                  <RefreshCw size={12} /> Try again
                </button>
              )}
            </div>
          )}
          {!loading && !error && projects.length > 2 && (
            <div className="relative mb-6">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-base-500" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search your projects…"
                className="w-full bg-base-900 border border-base-800/50 rounded-xl pl-9 pr-9 py-2.5 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent/30 focus:bg-base-850 transition-colors"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-lg text-base-500 hover:text-base-200 hover:bg-base-800 transition-colors"
                  aria-label="Clear search"
                >
                  <X size={14} />
                </button>
              )}
            </div>
          )}
          {!loading && !error && (
            projects.length === 0 ? (
              <div className="flex flex-col items-center text-center py-12">
                <NoviMascot size={64} expression="happy" />
                <p className="text-sm font-medium text-base-100 mt-4">Start one and I'll keep everything related together.</p>
                <p className="text-xs text-base-500 mt-1 max-w-xs">
                  Chats, context, files — all in one place.
                </p>
                <button
                  onClick={() => setShowForm(true)}
                  className="mt-4 flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
                >
                  <Plus size={14} />
                  Start project
                </button>
              </div>
            ) : filteredProjects.length === 0 ? (
              <div className="py-10 text-center">
                <p className="text-sm text-base-300">Nothing matching "{search}"</p>
                <button onClick={() => setSearch('')} className="mt-2 text-xs text-accent hover:text-accent-soft transition-colors">Clear search</button>
              </div>
            ) : (
              <div className="space-y-1">
              {filteredProjects.map(p => {
                const projectConvosForSearch = conversations.filter(c => (c as any).projectId === p.id || p.conversationIds.includes(c.id))
                const matchConvos = q ? projectConvosForSearch.filter(c => c.title.toLowerCase().includes(q)) : []
                return (
                  <div
                    key={p.id}
                    onClick={() => handleSelectProject(p.id)}
                    className="group flex items-center gap-3 px-3 py-3 rounded-xl cursor-pointer hover:bg-base-900/70 border border-transparent hover:border-base-800/30 transition-colors"
                  >
                    <span className="w-7 h-7 shrink-0 rounded-full bg-base-800 flex items-center justify-center text-[11px] font-medium text-base-400">
                      {p.name.charAt(0).toUpperCase()}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-base-100 truncate">{p.name}</p>
                      <p className="text-xs text-base-500 truncate">
                        {p.description || `${countByProject.get(p.id) ?? 0} conversation${(countByProject.get(p.id) ?? 0) !== 1 ? 's' : ''}`}
                      </p>
                      {matchConvos.length > 0 && (
                        <p className="text-[11px] text-accent/80 truncate mt-0.5">{matchConvos.length} matching conversation{matchConvos.length !== 1 ? 's' : ''}: {matchConvos.slice(0,2).map(c=>c.title).join(', ')}</p>
                      )}
                    </div>
                    <span className="hidden sm:inline text-[11px] text-base-600 shrink-0">{countByProject.get(p.id) ?? 0} chats</span>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteProject(p) }}
                      className="shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 p-1.5 rounded-lg text-base-500 hover:text-err hover:bg-base-800 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                      aria-label={`Delete project ${p.name}`}
                      title="Delete project"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                )
              })}
            </div>
            )
          )}
          </div>
        </div>

      <AnimatePresence>
        {showForm && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={() => setShowForm(false)}
          >
            <motion.div
              initial={{ opacity: 0, y: 12, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 12, scale: 0.97 }}
              transition={{ duration: 0.14, ease: 'easeOut' }}
              className="w-[520px] max-w-full rounded-2xl border border-base-700 bg-base-900 shadow-panel p-5"
              onClick={(e) => e.stopPropagation()}
            >
              <ProjectForm
                onSubmit={async (data) => {
                  const p = await onCreateProject(data.name, data.description, data.sharedContext)
                  if (p) handleSelectProject(p.id)
                  setShowForm(false)
                }}
                onCancel={() => setShowForm(false)}
              />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
