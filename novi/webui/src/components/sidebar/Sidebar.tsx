import { useState, useMemo, useRef, useEffect } from 'react'
import { motion } from 'framer-motion'
import { PanelLeftClose, PanelLeftOpen, Plus, Search, FolderKanban, ChevronRight, ChevronDown, MoreHorizontal, Pin, PinOff, Pencil, Trash2, Settings } from 'lucide-react'
import { Conversation, Project } from '@/types'
import { SidebarItem } from './SidebarItem'
import { NAV_ITEMS, NAV_ORDER, NavItemId } from './workspaceModes'
import { SearchModal } from '@/components/search/SearchModal'

interface Props {
  collapsed: boolean
  onToggleCollapse: () => void
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

export function Sidebar({ collapsed, onToggleCollapse, conversations, activeId, onSelect, onNewChat, onNewChatInProject, onPin, onRename, onDelete, projects, activeProjectId, onSelectProject, onCreateProject, onUpdateProject, onDeleteProject, activeSection, onSectionChange, jobsCount = 0, generatingConversationId = null }: Props) {
  const [searchOpen, setSearchOpen] = useState(false)
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
  const menuRef = useRef<HTMLDivElement>(null)

  const pinnedConvos = useMemo(() => conversations.filter((c) => c.pinned), [conversations])
  const pinnedProjects = useMemo(() => (projects ?? []).filter((p) => (p as any).pinned).sort((a,b) => (b.updatedAt || "").localeCompare(a.updatedAt || "")), [projects])
  const pinned = pinnedConvos
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
  const handleCreateProject = async () => {
    const name = window.prompt("Project name:")
    if (!name?.trim()) return
    const p = await onCreateProject?.(name.trim())
    if (p) setExpandedIds(prev => new Set([...prev, p.id]))
  }

  return (
    <motion.aside
      animate={{ width: collapsed ? 64 : 264 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="h-full flex flex-col border-r border-base-800 bg-base-900 shrink-0"
    >
      <div className="flex items-center justify-between px-3 h-14 shrink-0">
        <div className="flex items-center gap-2.5">
          <img src="/assets/Novi-sprite.svg" alt="Novi" className="w-auto h-8" style={{ imageRendering: 'pixelated' }} />
          {!collapsed && (
            <span className="font-semibold tracking-tight text-base-100 leading-tight">Novi</span>
          )}
        </div>
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
      </div>

      <div className="px-2 space-y-1">
        {NAV_ORDER.filter((id) => id !== 'settings' && id !== 'conversations' && id !== 'projects').map((id) => {
          const item = NAV_ITEMS[id]
          const Icon = item.icon
          return (
            <button
              key={id}
              onClick={() => onSectionChange(id)}
              className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-xl text-sm font-medium transition-colors ${
                activeSection === id
                  ? 'bg-base-800 text-base-100'
                  : 'text-base-400 hover:text-base-200 hover:bg-base-800/50'
              }`}
            >
              <Icon size={15} />
              {!collapsed && (
                <span className="flex-1 text-left">{item.label}</span>
              )}
              {!collapsed && id === 'jobs' && jobsCount > 0 && (
                <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-accent/20 text-accent">
                  {jobsCount}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <div className="flex flex-col min-h-0 flex-1">
        {!collapsed && activeSection === 'projects' && (
          <div className="flex-1 overflow-y-auto mt-3 px-2 space-y-1">
            <p className="px-2.5 text-[11px] uppercase tracking-wider text-base-500 mb-1">Projects</p>
            {(projects ?? []).length === 0 ? (
              <p className="px-2.5 text-xs text-base-600 py-2">No projects yet</p>
            ) : (
              (projects ?? []).map((p) => (
                <button
                  key={p.id}
                  onClick={() => onSelectProject?.(p.id)}
                  className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-xl text-sm transition-colors text-left ${activeProjectId === p.id ? 'bg-base-800 text-base-100' : 'text-base-400 hover:text-base-200 hover:bg-base-800/50'}`}
                >
                  <FolderKanban size={14} className={activeProjectId === p.id ? 'text-accent' : 'text-base-500'} />
                  <span className="flex-1 truncate">{p.name}</span>
                  <span className="text-[11px] text-base-500">{p.conversationIds.length}</span>
                </button>
              ))
            )}
          </div>
        )}
        {!collapsed && (
          <>
            <button
              onClick={onNewChat}
              className="mx-2 mt-2 flex items-center gap-2 px-2.5 py-2 rounded-xl text-sm font-medium text-base-300 hover:text-base-100 hover:bg-base-800/50 transition-colors focus-visible:ring-2 focus-visible:ring-accent/20 text-left"
            >
              <Plus size={14} />
              New chat
            </button>

            <button
              onClick={() => setSearchOpen(true)}
              className="mx-2 mt-1 flex items-center gap-2 px-2.5 py-2 rounded-xl text-base-300 hover:bg-base-800 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-accent/20 text-left"
            >
              <Search size={14} />
              Search
            </button>

            <div className="flex-1 overflow-y-auto mt-3 px-2 space-y-3">
              {(pinnedProjects.length > 0 || pinnedConvos.length > 0) && (
                <div>
                  <p className="px-2.5 text-[11px] uppercase tracking-wider text-accent mb-1">Pinned</p>
                  <div className="space-y-1">
                    {pinnedProjects.map((p) => (
                      <button
                        key={`pinned-proj-${p.id}`}
                        onClick={() => onSelectProject?.(p.id)}
                        className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-xl text-sm text-left ${activeProjectId === p.id ? 'bg-base-800 text-base-100' : 'text-base-300 hover:bg-base-800/50 hover:text-base-100'}`}
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
                    <span className="text-[11px] uppercase tracking-wider text-base-500 font-medium">Projects</span>
                    {(projects?.length ?? 0) > 0 && <span className="text-[11px] text-base-600">- {projects?.length}</span>}
                  </button>
                  <button
                    onClick={handleCreateProject}
                    aria-label="New project"
                    className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-800 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                    title="New project"
                  >
                    <Plus size={12} />
                  </button>
                  <button
                    onClick={() => onSectionChange('projects')}
                    aria-label="View all projects"
                    className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-800 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                    title="View all"
                  >
                    <MoreHorizontal size={12} />
                  </button>
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
                              <div className={`group flex items-center gap-1 px-2.5 py-1.5 rounded-xl ${activeProjectId === p.id ? 'bg-base-800 text-base-100' : 'text-base-400 hover:bg-base-800/50 hover:text-base-200'} transition-colors`}>
                                <button
                                  onClick={() => toggleProject(p.id)}
                                  aria-expanded={expanded}
                                  aria-label={`${expanded ? 'Collapse' : 'Expand'} ${p.name}`}
                                  className="p-0.5 rounded hover:bg-base-700 transition-colors focus-visible:ring-2 focus-visible:ring-accent/20"
                                >
                                  {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                </button>
                                <button
                                  onClick={() => onSelectProject?.(p.id)}
                                  className="flex-1 flex items-center gap-1.5 text-left min-w-0"
                                >
                                  <FolderKanban size={13} className={activeProjectId === p.id ? 'text-accent' : 'text-base-500'} />
                                  <span className="truncate text-xs font-medium">{p.name}</span>
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
                                        onClick={() => { onSelectProject?.(p.id); setProjectMenuId(null) }}
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
                                <div className="ml-3 mt-1 space-y-0.5 border-l border-base-800 pl-2">
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
                  className="w-full flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-base-400 hover:text-base-200 hover:bg-base-800/50 transition-colors focus-visible:ring-2 focus-visible:ring-accent/20 text-left"
                >
                  {chatsExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  <span className="text-[11px] uppercase tracking-wider font-medium">Chats</span>
                  <span className="text-[11px] text-base-600">- {unassigned.length}</span>
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

      {!collapsed && (
        <div className="px-2 pb-3 border-base-800 pt-2">
          {(() => {
            const Icon = NAV_ITEMS.settings.icon
            return (
              <button
                onClick={() => onSectionChange('settings')}
                className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-xl text-sm font-medium transition-colors ${
                  activeSection === 'settings'
                    ? 'bg-base-800 text-base-100'
                    : 'text-base-400 hover:text-base-200 hover:bg-base-800/50'
                }`}
              >
                <Icon size={15} />
                <span className="flex-1 text-left">{NAV_ITEMS.settings.label}</span>
              </button>
            )
          })()}
        </div>
      )}

      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} onSelect={onSelect} />
    </motion.aside>
  )
}
