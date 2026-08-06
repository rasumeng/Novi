import { useState, useMemo } from 'react'
import { motion } from 'framer-motion'
import { PanelLeftClose, PanelLeftOpen, Plus, Search } from 'lucide-react'
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
  activeSection: NavItemId
  onSectionChange: (id: NavItemId) => void
  jobsCount?: number
  /** Id of the conversation that owns the current generation, or null. */
  generatingConversationId?: string | null
}

export function Sidebar({ collapsed, onToggleCollapse, conversations, activeId, onSelect, onNewChat, onPin, onRename, onDelete, projects, activeSection, onSectionChange, jobsCount = 0, generatingConversationId = null }: Props) {
  const [searchOpen, setSearchOpen] = useState(false)

  const pinned = useMemo(() => conversations.filter((c) => c.pinned), [conversations])
  const recent = useMemo(() => conversations.filter((c) => !c.pinned), [conversations])

  return (
    <motion.aside
      animate={{ width: collapsed ? 64 : 264 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="h-full flex flex-col border-r border-base-800 bg-base-900 shrink-0"
    >
      <div className="flex items-center justify-between px-3 h-14 shrink-0">
        <div className="flex items-center gap-2.5">
          <img src="/assets/Cozmo-sprite.svg" alt="Cozmo" className="w-auto h-8" style={{ imageRendering: 'pixelated' }} />
          {!collapsed && (
            <span className="font-semibold tracking-tight text-base-100 leading-tight">Cozmo</span>
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
            </button>
          )
        })}
      </div>

      <div className="flex flex-col min-h-0 flex-1">
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

            <div className="flex-1 overflow-y-auto mt-3 px-2 space-y-1">
              {pinned.length > 0 && (
                <>
                  <p className="px-2.5 text-[11px] uppercase tracking-wider text-accent mb-1">Pinned</p>
                  {pinned.map((c) => (
                    <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
                  ))}
                  <div className="h-2" />
                </>
              )}
              <p className="px-2.5 text-[11px] uppercase tracking-wider text-base-500 mb-1">Recent</p>
              {recent.map((c) => (
                <SidebarItem key={c.id} conversation={c} active={c.id === activeId} generating={c.id === generatingConversationId} onClick={() => onSelect(c.id)} onPin={onPin} onRename={onRename} onDelete={onDelete} />
              ))}
            </div>
          </>
        )}
      </div>

      {!collapsed && (
        <div className="px-2 pb-3 border-t border-base-800 pt-2">
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
