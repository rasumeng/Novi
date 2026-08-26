import { MessageSquare, FolderKanban, PlayCircle, Settings, History, LucideIcon } from 'lucide-react'

export type NavItemId = 'conversations' | 'projects' | 'jobs' | 'timeline' | 'settings'

export interface NavItemConfig {
  label: string
  icon: LucideIcon
}

export const NAV_ITEMS: Record<NavItemId, NavItemConfig> = {
  conversations: { label: 'Conversations', icon: MessageSquare },
  projects: { label: 'Projects', icon: FolderKanban },
  jobs: { label: 'Jobs', icon: PlayCircle },
  timeline: { label: 'Timeline', icon: History },
  settings: { label: 'Settings', icon: Settings },
}

export const NAV_ORDER: NavItemId[] = ['conversations', 'projects', 'jobs', 'timeline', 'settings']
