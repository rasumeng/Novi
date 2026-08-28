import { useState, useMemo } from 'react'
import { motion } from 'framer-motion'
import { PanelLeftClose, PanelLeftOpen, Plus, Search, FolderKanban, ChevronRight, ChevronDown } from 'lucide-react'
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
  onPin: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  projects?: Project[]
  activeProjectId?: string | null
  onSelectProject?: (id: string) => void
  activeSection: NavItemId
  onSectionChange: (id: NavItemId) => void
  jobsCount?: number
  /** Id of the conversation that owns the current generation, or null. */
  generatingConversationId?: string | null
}

export function Sidebar({ collapsed, onToggleCollapse, conversations, activeId, onSelect, onNewChat, onPin, onRename, onDelete, projects, activeProjectId, onSelectProject, activeSection, onSectionChange, jobsCount = 0, generatingConversationId = null }: Props) {
  const [searchOpen, setSearchOpen] = useState(false)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem('novi_sidebar_expanded_projects')
      return raw ? new Set(JSON.parse(raw)) : new Set()
    } catch { return new Set() }
  })

  const pinned = useMemo(() => conversations.filter((c) => c.pinned), [conversations])
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
        {NAV_ORDER.filter((id) => id !== 'settings').map((id) => {
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
              {!collapsed && id === 'projects' && (projects?.length ?? 0) > 0 && (
                <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-base-700 text-base-300">
                  {projects?.length}
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
        {!collapsed && activeSection === 'conversations' && (
          <>
            <button
              onClick={onNewChat}
              className="mx-2 mt-2 flex items-center gap-2 px-2.5 py-2 rounded-xl bg-accent/90 hover:bg-accent text-white text-sm font-medium transition-colors"
            >
              <Plus size={16} />
              New Conversation
            </button>

            <button
              onClick={() => setSearchOpen(true)}
              className="mx-2 mt-1 flex items-center gap-2 px-2.5 py-2 rounded-xl text-base-300 hover:bg-base-800 text-sm transition-colors"
            >
              <Search size={15} />
              Search
            </button>

            <div className="flex-1 overflow-y-auto mt-3 px-2 space-y-3">
              {pinned.length > 0 && (
                <div>
                  <p className="px-2.5 text-[11px] uppercase tracking-wider text-accent mb-1">Pinned</p>
                  {pinned.map((c) => (
                    <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
                  ))}
                </div>
              )}
              {(projects ?? []).length > 0 && (
                <div>
                  <p className="px-2.5 text-[11px] uppercase tracking-wider text-base-500 mb-1">Projects</p>
                  <div className="space-y-1">
                    {(projects ?? []).map((p) => {
                      const chats = projectMap.get(p.id) ?? []
                      const expanded = expandedIds.has(p.id)
                      return (
                        <div key={p.id}>
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
                            </button>
                            <button
                              onClick={() => { onSelectProject?.(p.id); onNewChat() }}
                              aria-label={`New chat in ${p.name}`}
                              className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 rounded hover:bg-base-700 text-base-400 hover:text-base-100 transition-all focus-visible:ring-2 focus-visible:ring-accent/20"
                              title="New chat"
                            >
                              <Plus size={12} />
                            </button>
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
                  </div>
                </div>
              )}
              <div>
                <p className="px-2.5 text-[11px] uppercase tracking-wider text-base-500 mb-1">Chats</p>
                {unassigned.length === 0 ? (
                  <p className="px-2.5 text-xs text-base-600 py-1">No chats</p>
                ) : (
                  unassigned.map((c) => (
                    <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
                  ))
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
