import { Cpu, Bot, Brain, Puzzle, Cable, ShieldCheck, Settings, Server, Lightbulb, FileText, GitBranch, Globe, Database, Calendar, Mail, MessageSquare, Map, Search, Activity, Image, Cloud } from 'lucide-react'
import type { SectionId } from './types'

// Every section here renders real content — no placeholders. Ordering matches
// the locked M4 settings IA. There is no "Advanced" catch-all; Developer is
// the home for expert/internal configuration.
export const SECTIONS: { id: SectionId; label: string; icon: React.ElementType }[] = [
  { id: 'general', label: 'General', icon: Settings },
  { id: 'models', label: 'Models', icon: Cpu },
  { id: 'agent', label: 'Agent', icon: Bot },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'skills', label: 'Skills', icon: Puzzle },
  { id: 'connectors', label: 'Connectors', icon: Cable },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
  { id: 'developer', label: 'Developer', icon: Server },
]

export const PERM_MODES = ['allow', 'ask', 'deny'] as const

export const CAPABILITY_DEFS: Record<string, { label: string; icon: React.ElementType }> = {
  files: { label: "Files", icon: FileText },
  git: { label: "Git", icon: GitBranch },
  github: { label: "GitHub", icon: GitBranch },
  browser: { label: "Browser Automation", icon: Globe },
  database: { label: "Databases", icon: Database },
  memory: { label: "Long-term Memory", icon: Brain },
  reasoning: { label: "Reasoning", icon: Lightbulb },
  calendar: { label: "Calendar", icon: Calendar },
  email: { label: "Email", icon: Mail },
  communication: { label: "Communication", icon: MessageSquare },
  maps: { label: "Maps", icon: Map },
  "web-search": { label: "Web Search", icon: Search },
  monitoring: { label: "Monitoring", icon: Activity },
  "image-generation": { label: "Image Generation", icon: Image },
  infrastructure: { label: "Infrastructure", icon: Server },
  "cloud-storage": { label: "Cloud Storage", icon: Cloud },
}

export const PERMISSION_DEFS: Record<string, { label: string; key: string }[]> = {
  files: [
    { label: 'Read & Search', key: 'read' },
    { label: 'Write Files', key: 'write' },
    { label: 'Delete Files', key: 'delete' },
  ],
  git: [
    { label: 'Read Repos', key: 'read' },
    { label: 'Commit & Push', key: 'write' },
  ],
  github: [
    { label: 'Read Issues & PRs', key: 'read' },
    { label: 'Create & Edit', key: 'write' },
    { label: 'Merge & Approve', key: 'approve' },
    { label: 'Delete Branches', key: 'delete' },
  ],
  database: [
    { label: 'Read Queries', key: 'read' },
    { label: 'Write Queries', key: 'write' },
  ],
  browser: [
    { label: 'Navigate', key: 'navigate' },
    { label: 'Get Content', key: 'read' },
    { label: 'Interact (click, type)', key: 'interact' },
  ],
  _default: [
    { label: 'Allow Execution', key: 'execute' },
  ],
}
