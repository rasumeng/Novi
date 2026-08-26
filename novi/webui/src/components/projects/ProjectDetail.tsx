import { useState } from 'react'
import { ArrowLeft, ExternalLink, Edit3, Save, X } from 'lucide-react'
import { Project, Conversation } from '@/types'

interface Props {
  project: Project
  conversations: Conversation[]
  onBack: () => void
  onUpdate: (id: string, data: Partial<Project>) => void
  onSelectConversation: (id: string) => void
  onRemoveConversation: (convId: string, projId: string) => void
}

export function ProjectDetail({ project, conversations, onBack, onUpdate, onSelectConversation, onRemoveConversation }: Props) {
  const [editingContext, setEditingContext] = useState(false)
  const [contextValue, setContextValue] = useState(project.sharedContext)

  const saveContext = () => {
    onUpdate(project.id, { sharedContext: contextValue })
    setEditingContext(false)
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

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-base-400 mb-2">
            Conversations ({project.conversationIds.length})
          </h3>
          {project.conversationIds.length === 0 ? (
            <p className="text-sm text-base-500 italic">No conversations linked to this project yet.</p>
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
