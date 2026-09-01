import { useState, useMemo, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, FolderKanban, ChevronRight, ChevronDown, MoreHorizontal, Pin, PinOff, Pencil, Trash2, Settings, LayoutGrid, ChevronsUpDown } from 'lucide-react'
import { Conversation, Project } from '@/types'
import { SidebarItem } from './SidebarItem'
import { NAV_ITEMS, NAV_ORDER, NavItemId } from './workspaceModes'
import { ProjectForm } from '@/components/projects/ProjectForm'

interface Props {
  collapsed: boolean
  conversations: Conversation[]
  activeId: string
  onSelect: (id: string) => void
  onNewChat: () => void
  onNewChatInProject?: (projectId: string) => void
  onPin: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  projects?: Project[]
  activeProjectId?: string | null
  onSelectProject?: (id: string) => void
  onCreateProject?: (name: string, description?: string, sharedContext?: string) => Promise<Project | null>
  onUpdateProject?: (id: string, data: Partial<Project>) => Promise<Project | null>
  onDeleteProject?: (id: string) => void
  activeSection: NavItemId
  onSectionChange: (id: NavItemId) => void
  jobsCount?: number
  generatingConversationId?: string | null
}

export function Sidebar({ collapsed, conversations, activeId, onSelect, onNewChat, onNewChatInProject, onPin, onRename, onDelete, projects, activeProjectId, onSelectProject, onCreateProject, onUpdateProject, onDeleteProject, activeSection, onSectionChange, jobsCount = 0, generatingConversationId = null }: Props) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem('novi_sidebar_expanded_projects')
      return raw ? new Set(JSON.parse(raw)) : new Set()
    } catch { return new Set() }
  })
  const [projectsExpanded, setProjectsExpanded] = useState(() => {
    try { return localStorage.getItem('novi_sidebar_projects_expanded') !== 'false' } catch { return true }
  })
  const [chatsExpanded, setChatsExpanded] = useState(() => {
    try { return localStorage.getItem('novi_sidebar_chats_expanded') !== 'false' } catch { return true }
  })
  const [showMoreProjects, setShowMoreProjects] = useState(false)
  const [projectMenuId, setProjectMenuId] = useState<string | null>(null)
  const [renamingProjectId, setRenamingProjectId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [headerMenuOpen, setHeaderMenuOpen] = useState(false)
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const headerMenuRef = useRef<HTMLDivElement>(null)

  const pinnedConvos = useMemo(() => conversations.filter((c) => c.pinned), [conversations])
  const pinnedProjects = useMemo(() => (projects ?? []).filter((p) => (p as any).pinned).sort((a,b) => (b.updatedAt || "").localeCompare(a.updatedAt || "")), [projects])
  const sortedProjects = useMemo(() => {
    const list = [...(projects ?? [])]
    list.sort((a,b) => {
      if (!!(a as any).pinned !== !!(b as any).pinned) return (a as any).pinned ? -1 : 1
      return (b.updatedAt || "").localeCompare(a.updatedAt || "")
    })
    return list
  }, [projects])
  const visibleProjects = showMoreProjects ? sortedProjects : sortedProjects.slice(0, 5)
  const projectMap = useMemo(() => {
    const map = new Map<string, Conversation[]>()
    for (const p of projects ?? []) map.set(p.id, [])
    for (const c of conversations) {
      if (c.pinned) continue
      const pid = (c as any).projectId as string | undefined
      let owner: string | null = null
      if (pid && map.has(pid)) owner = pid
      else {
        for (const p of projects ?? []) {
          if (p.conversationIds.includes(c.id)) { owner = p.id; break }
        }
      }
      if (owner) map.get(owner)!.push(c)
    }
    return map
  }, [conversations, projects])
  const unassigned = useMemo(() => {
    const assigned = new Set<string>()
    for (const list of projectMap.values()) for (const c of list) assigned.add(c.id)
    return conversations.filter((c) => !c.pinned && !assigned.has(c.id))
  }, [conversations, projectMap])

  const toggleProject = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      try { localStorage.setItem('novi_sidebar_expanded_projects', JSON.stringify([...next])) } catch {}
      return next
    })
  }
  const toggleProjectsSection = () => {
    setProjectsExpanded(v => {
      try { localStorage.setItem('novi_sidebar_projects_expanded', String(!v)) } catch {}
      return !v
    })
  }
  const toggleChatsSection = () => {
    setChatsExpanded(v => {
      try { localStorage.setItem('novi_sidebar_chats_expanded', String(!v)) } catch {}
      return !v
    })
  }
  useEffect(() => {
    if (!projectMenuId) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setProjectMenuId(null)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [projectMenuId])
  useEffect(() => {
    if (!headerMenuOpen) return
    const close = (e: MouseEvent) => {
      if (headerMenuRef.current && !headerMenuRef.current.contains(e.target as Node)) setHeaderMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [headerMenuOpen])
  const handleCreateProject = () => setNewProjectOpen(true)
  const handleNewProjectSubmit = async (data: { name: string; description: string; sharedContext: string }) => {
    const p = await onCreateProject?.(data.name, data.description, data.sharedContext)
    if (p) {
      setExpandedIds(prev => new Set([...prev, p.id]))
      if (!projectsExpanded) toggleProjectsSection()
    }
    setNewProjectOpen(false)
  }

  const uniformItem = (active: boolean) =>
    `w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-[13px] font-normal transition-colors ${
      active ? 'bg-base-800 text-base-100' : 'text-base-400 hover:text-base-100 hover:bg-base-800/40'
    }`

  return (
    <motion.aside
      animate={{ width: collapsed ? 56 : 260 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="h-full flex flex-col bg-base-950 border-r border-base-800/30 shrink-0"
    >
      <div className="flex flex-col flex-1 min-h-0">
        {collapsed ? (
          <div className="flex flex-col items-center gap-1 px-1.5 py-2">
            <button
              onClick={onNewChat}
              aria-label="New chat"
              title="New chat"
              className="w-8 h-8 flex items-center justify-center rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800/60 transition-colors"
            >
              <Plus size={15} />
            </button>
            {NAV_ORDER.filter((id) => id !== 'settings' && id !== 'conversations' && id !== 'projects').map((id) => {
              const item = NAV_ITEMS[id]
              const Icon = item.icon
              const active = activeSection === id
              return (
                <button
                  key={id}
                  onClick={() => onSectionChange(id)}
                  aria-label={item.label}
                  title={item.label}
                  className={`w-8 h-8 flex items-center justify-center rounded-lg transition-colors ${active ? 'bg-base-800 text-base-100' : 'text-base-400 hover:text-base-100 hover:bg-base-800/40'}`}
                >
                  <Icon size={15} />
                </button>
              )
            })}
            {(pinnedProjects.length > 0 || pinnedConvos.length > 0) && (
              <div className="w-6 border-t border-base-800/30 my-1" />
            )}
            <button
              onClick={() => onSectionChange('projects')}
              aria-label="Projects"
              title="Projects"
              className={`w-8 h-8 flex items-center justify-center rounded-lg transition-colors ${activeSection === 'projects' ? 'bg-base-800 text-base-100' : 'text-base-400 hover:text-base-100 hover:bg-base-800/40'}`}
            >
              <FolderKanban size={15} />
            </button>
          </div>
        ) : (
          <>
            <div className="px-1.5 py-2">
              <button
                onClick={onNewChat}
                className={uniformItem(false)}
              >
                <Plus size={14} className="shrink-0" />
                <span className="flex-1 text-left">New chat</span>
              </button>
              {NAV_ORDER.filter((id) => id !== 'settings' && id !== 'conversations' && id !== 'projects').map((id) => {
                const item = NAV_ITEMS[id]
                const Icon = item.icon
                const active = activeSection === id
                return (
                  <button
                    key={id}
                    onClick={() => onSectionChange(id)}
                    className={uniformItem(active)}
                  >
                    <Icon size={14} className="shrink-0" />
                    <span className="flex-1 text-left">{item.label}</span>
                    {id === 'jobs' && jobsCount > 0 && (
                      <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-accent/15 text-accent">
                        {jobsCount}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>

            <div className="flex-1 overflow-y-auto px-1.5 space-y-4 mt-2">
              {(pinnedProjects.length > 0 || pinnedConvos.length > 0) && (
                <div>
                  <p className="px-2.5 text-[10px] uppercase tracking-widest text-base-500 mb-1.5">Pinned</p>
                  <div className="space-y-1">
                    {pinnedProjects.map((p) => (
                      <button
                        key={`pinned-proj-${p.id}`}
                        onClick={() => onSelectProject?.(p.id)}
                        className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[13px] text-left ${activeProjectId === p.id ? 'bg-base-800 text-base-100' : 'text-base-400 hover:bg-base-800/40 hover:text-base-100'}`}
                      >
                        <Pin size={11} className="text-accent shrink-0" />
                        <FolderKanban size={13} className="text-base-500 shrink-0" />
                        <span className="truncate flex-1">{p.name}</span>
                      </button>
                    ))}
                    {pinnedConvos.map((c) => (
                      <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
                    ))}
                  </div>
                </div>
              )}
              <div>
                <div className="group flex items-center gap-1 px-2.5 py-1">
                  <button
                    onClick={toggleProjectsSection}
                    aria-expanded={projectsExpanded}
                    className="flex items-center gap-1.5 flex-1 text-left focus-visible:ring-2 focus-visible:ring-accent/20 rounded"
                  >
                    {projectsExpanded ? <ChevronDown size={12} className="text-base-500" /> : <ChevronRight size={12} className="text-base-500" />}
                    <span className="text-[10px] uppercase tracking-widest text-base-500 font-medium">Projects</span>
                    {(projects?.length ?? 0) > 0 && <span className="text-[10px] text-base-600">- {projects?.length}</span>}
                  </button>
                  <button
                    onClick={handleCreateProject}
                    aria-label="New project"
                    className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-800 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                    title="New project"
                  >
                    <Plus size={12} />
                  </button>
                  <div className="relative">
                    <button
                      onClick={() => setHeaderMenuOpen(v => !v)}
                      aria-label="Projects menu"
                      aria-expanded={headerMenuOpen}
                      className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-800 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                      title="Projects menu"
                    >
                      <MoreHorizontal size={12} />
                    </button>
                    {headerMenuOpen && (
                      <div ref={headerMenuRef} className="absolute right-0 top-full mt-1 w-48 rounded-xl border border-base-700 bg-base-850 shadow-lg z-50 py-1">
                        <button
                          onClick={() => { setHeaderMenuOpen(false); onSectionChange('projects') }}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                        >
                          <LayoutGrid size={13} /> View all projects
                        </button>
                        <button
                          onClick={() => { setHeaderMenuOpen(false); setNewProjectOpen(true) }}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                        >
                          <Plus size={13} /> New project
                        </button>
                        <div className="border-t border-base-700 my-1" />
                        <button
                          onClick={() => {
                            setHeaderMenuOpen(false)
                            if (projectsExpanded) {
                              setProjectsExpanded(false)
                              try { localStorage.setItem('novi_sidebar_projects_expanded', 'false') } catch {}
                            } else {
                              setProjectsExpanded(true)
                              try { localStorage.setItem('novi_sidebar_projects_expanded', 'true') } catch {}
                            }
                          }}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                        >
                          <ChevronsUpDown size={13} /> {projectsExpanded ? 'Collapse projects' : 'Expand projects'}
                        </button>
                        <button
                          onClick={() => {
                            setHeaderMenuOpen(false)
                            const allIds = (projects ?? []).map(p => p.id)
                            if (expandedIds.size === allIds.length && allIds.length > 0) {
                              setExpandedIds(new Set())
                              try { localStorage.setItem('novi_sidebar_expanded_projects', JSON.stringify([])) } catch {}
                            } else {
                              setExpandedIds(new Set(allIds))
                              try { localStorage.setItem('novi_sidebar_expanded_projects', JSON.stringify(allIds)) } catch {}
                            }
                          }}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                        >
                          <ChevronDown size={13} /> {expandedIds.size > 0 && expandedIds.size === (projects?.length ?? 0) ? 'Collapse all' : 'Expand all'}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                {projectsExpanded && (
                  <div className="space-y-1 mt-1">
                    {(projects ?? []).length === 0 ? (
                      <p className="px-2.5 text-xs text-base-600 py-1">No projects yet</p>
                    ) : (
                      <>
                        {visibleProjects.map((p) => {
                          const chats = projectMap.get(p.id) ?? []
                          const expanded = expandedIds.has(p.id)
                          const isPinned = !!(p as any).pinned
                          return (
                            <div key={p.id} className="group/project">
                              <div className={`group flex items-center gap-1 px-2.5 py-1.5 rounded-lg ${activeProjectId === p.id ? 'bg-base-800 text-base-100' : 'text-base-400 hover:bg-base-800/40 hover:text-base-200'} transition-colors`}>
                                <button
                                  onClick={() => toggleProject(p.id)}
                                  aria-expanded={expanded}
                                  aria-label={`${expanded ? 'Collapse' : 'Expand'} ${p.name}`}
                                  className="p-0.5 rounded hover:bg-base-700 transition-colors focus-visible:ring-2 focus-visible:ring-accent/20"
                                >
                                  {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                </button>
                                <button
                                  onClick={() => toggleProject(p.id)}
                                  className="flex-1 flex items-center gap-1.5 text-left min-w-0"
                                  aria-label={`${expanded ? 'Collapse' : 'Expand'} ${p.name}`}
                                >
                                  <FolderKanban size={13} className={activeProjectId === p.id ? 'text-accent' : 'text-base-500'} />
                                  <span className="truncate text-[13px] font-normal">{p.name}</span>
                                  <span className="text-[11px] text-base-500">{chats.length}</span>
                                  {isPinned && <Pin size={10} className="text-accent shrink-0" />}
                                </button>
                                <button
                                  onClick={() => { if (onNewChatInProject) onNewChatInProject(p.id); else { onSelectProject?.(p.id); onNewChat() } }}
                                  aria-label={`New chat in ${p.name}`}
                                  className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-700 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                                  title="New chat"
                                >
                                  <Plus size={12} />
                                </button>
                                <div className="relative">
                                  <button
                                    onClick={() => setProjectMenuId(projectMenuId === p.id ? null : p.id)}
                                    aria-label={`Project menu ${p.name}`}
                                    aria-expanded={projectMenuId === p.id}
                                    className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-700 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                                  >
                                    <MoreHorizontal size={12} />
                                  </button>
                                  {projectMenuId === p.id && (
                                    <div ref={menuRef} className="absolute right-0 top-full mt-1 w-44 rounded-xl border border-base-700 bg-base-850 shadow-lg z-50 py-1">
                                      {renamingProjectId === p.id ? (
                                        <div className="px-2 py-1">
                                          <input
                                            autoFocus
                                            value={renameValue}
                                            onChange={(e) => setRenameValue(e.target.value)}
                                            onKeyDown={(e) => {
                                              if (e.key === 'Enter') {
                                                const v = renameValue.trim()
                                                if (v) onUpdateProject?.(p.id, { name: v })
                                                setRenamingProjectId(null)
                                                setProjectMenuId(null)
                                              }
                                              if (e.key === 'Escape') setRenamingProjectId(null)
                                            }}
                                            onBlur={() => setRenamingProjectId(null)}
                                            placeholder="Project name"
                                            className="w-full bg-base-800 border border-base-700 rounded-lg px-2 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40"
                                          />
                                        </div>
                                      ) : (
                                        <button
                                          onClick={() => { setRenameValue(p.name); setRenamingProjectId(p.id) }}
                                          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                                        >
                                          <Pencil size={13} /> Rename
                                        </button>
                                      )}
                                      <button
                                        onClick={async () => {
                                          await onUpdateProject?.(p.id, { pinned: !isPinned } as any)
                                          setProjectMenuId(null)
                                        }}
                                        className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                                      >
                                        {isPinned ? <PinOff size={13} /> : <Pin size={13} />}
                                        {isPinned ? 'Unpin' : 'Pin'}
                                      </button>
                                      <button
                                        onClick={() => {
                                          setExpandedIds(prev => {
                                            const next = new Set(prev)
                                            next.add(p.id)
                                            try { localStorage.setItem('novi_sidebar_expanded_projects', JSON.stringify([...next])) } catch {}
                                            return next
                                          })
                                          onSelectProject?.(p.id)
                                          setProjectMenuId(null)
                                        }}
                                        className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
                                      >
                                        <Settings size={13} /> Settings
                                      </button>
                                      <div className="border-t border-base-700 my-1" />
                                      <button
                                        onClick={() => { onDeleteProject?.(p.id); setProjectMenuId(null) }}
                                        className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-err hover:bg-base-800"
                                      >
                                        <Trash2 size={13} /> Delete
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </div>
                              {expanded && chats.length > 0 && (
                                <div className="ml-3 mt-1 space-y-0.5 border-l border-base-800/50 pl-2">
                                  {chats.map((c) => (
                                    <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
                                  ))}
                                </div>
                              )}
                              {expanded && chats.length === 0 && (
                                <p className="ml-6 text-[11px] text-base-600 py-1">No chats yet</p>
                              )}
                            </div>
                          )
                        })}
                        {(projects?.length ?? 0) > 5 && (
                          <button
                            onClick={() => setShowMoreProjects(v => !v)}
                            className="w-full text-left px-2.5 py-1.5 text-xs text-accent hover:text-accent/80 transition-colors"
                          >
                            {showMoreProjects ? 'Show less' : `Show more (${(projects?.length ?? 0) - 5})`}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
              <div className="mt-2">
                <button
                  onClick={toggleChatsSection}
                  aria-expanded={chatsExpanded}
                  className="w-full flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-base-400 hover:text-base-200 hover:bg-base-800/40 transition-colors focus-visible:ring-2 focus-visible:ring-accent/20 text-left"
                >
                  {chatsExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  <span className="text-[10px] uppercase tracking-widest font-medium">Chats</span>
                  <span className="text-[10px] text-base-600">- {unassigned.length}</span>
                </button>
                {chatsExpanded && (
                  <div className="mt-1 px-2 space-y-0.5">
                    {unassigned.length === 0 ? (
                      <p className="px-2.5 text-xs text-base-600 py-1">No chats</p>
                    ) : (
                      unassigned.map((c) => (
                        <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      <AnimatePresence>
        {newProjectOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={() => setNewProjectOpen(false)}
          >
            <motion.div
              initial={{ opacity: 0, y: 12, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 12, scale: 0.97 }}
              transition={{ duration: 0.14, ease: 'easeOut' }}
              className="w-[480px] max-w-full rounded-2xl border border-base-700 bg-base-900 shadow-panel p-5"
              onClick={(e) => e.stopPropagation()}
            >
              <ProjectForm onSubmit={handleNewProjectSubmit} onCancel={() => setNewProjectOpen(false)} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.aside>
  )
}
