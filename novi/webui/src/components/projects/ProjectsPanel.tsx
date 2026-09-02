import { useState, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, FolderKanban, Trash2, Search, X } from 'lucide-react'
import { Project, Conversation } from '@/types'
import { ProjectForm } from './ProjectForm'
import { ProjectDetail } from './ProjectDetail'
import { useConfirm } from '@/hooks/useConfirm'
import { EmptyState } from '@/components/common/EmptyState'

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
}: Props) {
  const { confirm, dialog } = useConfirm()
  const [showForm, setShowForm] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(activeProjectId ?? null)

  useEffect(() => {
    if (activeProjectId && projects.some(p => p.id === activeProjectId)) {
      setSelectedProjectId(activeProjectId)
    } else if (!activeProjectId) {
      if (selectedProjectId && !projects.some(p => p.id === selectedProjectId)) setSelectedProjectId(null)
    }
  }, [activeProjectId, projects])

  const q = search.trim().toLowerCase()
  const filteredProjects = useMemo(() => {
    if (!q) return projects
    return projects.filter(p => {
      if (p.name.toLowerCase().includes(q)) return true
      if (p.description?.toLowerCase().includes(q)) return true
      if (p.sharedContext?.toLowerCase().includes(q)) return true
      for (const cid of p.conversationIds) {
        const c = conversations.find(x => x.id === cid)
        if (c?.title.toLowerCase().includes(q)) return true
        // also search first message content
        const first = c?.messages[0]?.content.toLowerCase() ?? ''
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
    setSelectedProjectId(null)
    onSelectProject(null)
  }

  const handleSelectProject = (id: string) => {
    setSelectedProjectId(id)
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

      <header className="h-11 shrink-0 flex items-center justify-between px-4 gap-2">
        <h1 className="text-sm font-medium text-base-100">
          Projects <span className="text-xs font-normal text-base-500 ml-1.5">{projects.length > 0 ? `${projects.length}` : ''}</span>
        </h1>
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          New
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-2xl mx-auto">
          {projects.length > 0 && (
            <div className="relative mb-6">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-base-500" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search projects or conversations…"
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
          {projects.length === 0 ? (
            <EmptyState
              icon={FolderKanban}
              title="No projects yet"
              description="Create a project to group related conversations."
              action={
                <button
                  onClick={() => setShowForm(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
                >
                  <Plus size={14} />
                  New project
                </button>
              }
            />
          ) : filteredProjects.length === 0 ? (
            <div className="py-10 text-center">
              <p className="text-sm text-base-300">No matches for “{search}”</p>
              <button onClick={() => setSearch('')} className="mt-2 text-xs text-accent hover:text-accent-soft transition-colors">Clear search</button>
            </div>
          ) : (
              <div className="space-y-1">
              {filteredProjects.map(p => {
                const matchConvos = q ? p.conversationIds.map(cid => conversations.find(c => c.id === cid)).filter(c => c && c.title.toLowerCase().includes(q)) as typeof conversations : []
                return (
                  <div
                    key={p.id}
                    onClick={() => handleSelectProject(p.id)}
                    className="group flex items-center gap-3 px-3 py-3 rounded-xl cursor-pointer hover:bg-base-900/70 border border-transparent hover:border-base-800/30 transition-colors"
                  >
                    <div className="w-9 h-9 shrink-0 rounded-lg bg-base-900 group-hover:bg-base-850 flex items-center justify-center text-accent border border-base-800/30">
                      <FolderKanban size={15} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-base-100 truncate">{p.name}</p>
                      <p className="text-xs text-base-500 truncate">
                        {p.description || `${p.conversationIds.length} conversation${p.conversationIds.length !== 1 ? 's' : ''}`}
                      </p>
                      {matchConvos.length > 0 && (
                        <p className="text-[11px] text-accent/80 truncate mt-0.5">{matchConvos.length} matching conversation{matchConvos.length !== 1 ? 's' : ''}: {matchConvos.slice(0,2).map(c=>c.title).join(', ')}</p>
                      )}
                    </div>
                    <span className="hidden sm:inline text-[11px] text-base-600 shrink-0">{p.conversationIds.length} chats</span>
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