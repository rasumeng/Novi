// ProjectHome.tsx — hub-only view: composer + chat list, no header, no details pane
import { useState, useMemo, useRef, useEffect } from 'react'
import { X, Send, MessageSquareText, MoveRight, Trash2, MoreHorizontal, Settings, Pin, PinOff } from 'lucide-react'
import { Project, Conversation } from '@/types'
import { NoviMascot } from '@/components/brand/NoviMascot'

interface Props {
  project: Project
  conversations: Conversation[]
  onSelectConversation: (id: string) => void
  onRemoveConversation: (convId: string, projId: string) => void
  onSendInProject?: (projectId: string, content: string) => void
  activeConversationId?: string | null
  connection?: string
  generating?: boolean
  onStop?: () => void
  onOpenFull?: (id: string) => void
  onUpdate?: (id: string, data: Partial<Project>) => Promise<Project | null>
  onOpenSettings?: (projectId: string) => void
}

export function ProjectHome({
  project,
  conversations,
  onSelectConversation,
  onRemoveConversation,
  onSendInProject,
  connection,
  generating,
  onStop,
  onOpenFull,
  onUpdate,
  onOpenSettings,
}: Props) {
  const [composer, setComposer] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  const projectConvos = useMemo(() => {
    const ids = new Set(project.conversationIds)
    const list = conversations.filter(c => (c as any).projectId === project.id || ids.has(c.id))
    return [...list].sort((a,b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
  }, [project.conversationIds, project.id, conversations])

  const handleSend = () => {
    const text = composer.trim()
    if (!text || generating) return
    if (onSendInProject) {
      onSendInProject(project.id, text)
      setComposer('')
      textareaRef.current?.focus()
    }
  }

  const handleOpenChat = (id: string) => {
    if (onOpenFull) onOpenFull(id)
    else onSelectConversation(id)
  }

  const isPinned = !!(project as any).pinned

  const handlePin = async () => {
    await onUpdate?.(project.id, { pinned: !isPinned } as any)
    setMenuOpen(false)
  }

  const handleOpenSettings = () => {
    setMenuOpen(false)
    onOpenSettings?.(project.id)
  }

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
      <div className="flex-1 overflow-y-auto">
        <div className="min-h-full flex flex-col justify-center px-6 py-8">
          <div className="max-w-3xl mx-auto w-full">
            {/* Top row: Novi left, ... right — centered like LandingPage */}
            <div className="flex items-center justify-between gap-3 mb-6">
              <div className="flex items-center gap-3 min-w-0">
                <div className="shrink-0">
                  <NoviMascot size={64} expression="happy" />
                </div>
                <p className="text-[24px] font-semibold text-base-100 leading-snug truncate">
                  Let's talk inside {project.name}.
                </p>
              </div>
              <div className="relative shrink-0" ref={menuRef}>
                <button
                  onClick={() => setMenuOpen(v => !v)}
                  aria-label="Project menu"
                  aria-expanded={menuOpen}
                  className="p-2 rounded-full bg-base-900 border border-base-700 text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
                  title="Project menu"
                >
                  <MoreHorizontal size={16} />
                </button>
                {menuOpen && (
                  <div className="absolute right-0 top-full mt-2 w-44 rounded-xl border border-base-700 bg-base-850 shadow-lg z-50 py-1">
                    <button
                      onClick={handleOpenSettings}
                      className="w-full flex items-center gap-2 px-3 py-2 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                    >
                      <Settings size={14} /> Project settings
                    </button>
                    <button
                      onClick={handlePin}
                      className="w-full flex items-center gap-2 px-3 py-2 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                    >
                      {isPinned ? <PinOff size={14} /> : <Pin size={14} />}
                      {isPinned ? 'Unpin project' : 'Pin project'}
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* Composer right below top row */}
            <div className="w-full mb-6">
              {composerBar}
            </div>

            {/* Chat list beneath composer */}
            <div className="w-full">
              {projectConvos.length === 0 ? (
                <div className="py-8 text-center">
                  <p className="text-sm text-base-500">No chats in this project yet</p>
                  <p className="text-xs text-base-600 mt-1">Send a message above to start the first one.</p>
                </div>
              ) : (
                <>
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
    </div>
  )
}
