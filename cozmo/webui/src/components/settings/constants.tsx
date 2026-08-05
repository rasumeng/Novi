import { Cpu, Wrench, Brain, Puzzle, Cable, Settings, Server, Lightbulb, FileText, GitBranch, Globe, Database, Calendar, Mail, MessageSquare, Map, Search, Activity, Image, Cloud } from 'lucide-react'
import type { SectionId } from './types'

// Every section here renders real content — no placeholders. (Knowledge,
// Integrations, and Appearance were removed rather than shipped as dead ends;
// re-add them once there's an actual feature behind them.)
export const SECTIONS: { id: SectionId; label: string; icon: React.ElementType }[] = [
  { id: 'general', label: 'General', icon: Settings },
  { id: 'models', label: 'Models', icon: Cpu },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'mcp', label: 'Connectors', icon: Cable },
  { id: 'skills', label: 'Skills', icon: Puzzle },
  { id: 'advanced', label: 'Advanced', icon: Server },
]

export const BUILTIN_ROLES = ['classifier', 'router', 'orchestrator', 'chat', 'coder', 'planner', 'vision'] as const

export const PRESET_META: Record<string, { label: string; desc: string }> = {
  classifier: { label: 'Classifier', desc: 'Intent detection & message classification' },
  router: { label: 'Router', desc: 'Task routing and capability dispatch' },
  orchestrator: { label: 'Orchestrator', desc: 'Multi-step plan generation' },
  chat: { label: 'Chat', desc: 'General conversation & Q&A' },
  coder: { label: 'Coder', desc: 'Code generation & editing' },
  planner: { label: 'Planner', desc: 'Deep research & task planning' },
  vision: { label: 'Vision', desc: 'Image analysis & vision tasks' },
}

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
