import { useState, useEffect } from 'react'
import { Plus, FolderKanban, Trash2, Box } from 'lucide-react'
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
}: Props) {
  const { confirm, dialog } = useConfirm()
  const [showForm, setShowForm] = useState(false)
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(activeProjectId ?? null)

  // Sync sidebar-driven navigation (Settings → open detail) and clear on project delete
  useEffect(() => {
    if (activeProjectId && projects.some(p => p.id === activeProjectId)) {
      setSelectedProjectId(activeProjectId)
    } else if (!activeProjectId) {
      // don't auto-clear when user is browsing detail; only clear if current selection was deleted externally
      if (selectedProjectId && !projects.some(p => p.id === selectedProjectId)) setSelectedProjectId(null)
    }
  }, [activeProjectId, projects])

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
      />
    )
  }

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-base-950">
      {dialog}
      <header className="h-11 shrink-0 flex items-center justify-end px-4">
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          New Project
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {showForm && (
          <div className="mb-6 p-4 rounded-xl border border-base-700 bg-base-900">
            <ProjectForm
              onSubmit={async (data) => {
                await onCreateProject(data.name, data.description, data.sharedContext)
                setShowForm(false)
              }}
              onCancel={() => setShowForm(false)}
            />
          </div>
        )}

        {projects.length === 0 && !showForm ? (
          <EmptyState
            icon={Box}
            title="No projects yet"
            description="Create a project to group related conversations."
            action={
              <button
                onClick={() => setShowForm(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
              >
                <Plus size={14} />
                New Project
              </button>
            }
          />
        ) : (
          <div className="space-y-2 max-w-2xl">
            {projects.map(p => (
              <div
                key={p.id}
                className="group flex items-center gap-3 px-4 py-3 rounded-xl bg-base-900 border border-base-800 hover:border-base-700 transition-colors cursor-pointer"
                onClick={() => handleSelectProject(p.id)}
              >
                <FolderKanban size={18} className="text-accent shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-base-100 truncate">{p.name}</p>
                  {p.description && (
                    <p className="text-xs text-base-500 truncate">{p.description}</p>
                  )}
                  <p className="text-[11px] text-base-600 mt-0.5">{p.conversationIds.length} conversation{p.conversationIds.length !== 1 ? 's' : ''}</p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDeleteProject(p) }}
                  className="shrink-0 opacity-100 md:opacity-0 md:group-hover:opacity-100 focus:opacity-100 p-1.5 rounded-lg text-base-400 hover:text-err hover:bg-base-800 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                  aria-label={`Delete project ${p.name}`}
                  title="Delete project"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
