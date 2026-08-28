export interface Attachment {
  id: string
  type: 'image' | 'file'
  name: string
  mime: string
  size: number
  url: string
  thumbnail?: string
}

export type Role = 'user' | 'assistant'

export interface ChatMessage {
  id: string
  role: Role
  content: string
  createdAt: string
  model?: string
  citations?: string[]
  streaming?: boolean
  attachments?: Attachment[]
  /** Reasoning/thinking trace emitted before the visible answer. */
  thought?: string
  thoughtElapsedMs?: number
}

export interface Conversation {
  id: string
  title: string
  updatedAt: string
  pinned: boolean
  messages: ChatMessage[]
  projectId?: string | null
}

// Milestone 4 — assistant timeline + knowledge overview (user-facing shapes).
export interface TimelineEntry {
  /** Per-row instance id (timeline address, never a Brain id). */
  id: string
  kind: string
  title: string
  detail: string
  timestamp: string
  /** Pseudo id a user can open; may not resolve if the source chat was created outside this app. */
  conversation_id?: string
}

export interface KnowledgeEntry {
  content: string
  evidence: string
}

export interface KnowledgeCategory {
  category: string
  label: string
  entries: KnowledgeEntry[]
}

export interface KnowledgeOverview {
  categories: KnowledgeCategory[]
  total: number
  updated: string
}

export interface Project {
  id: string
  name: string
  description: string
  conversationIds: string[]
  sharedContext: string
  createdAt: string
  updatedAt: string
}

export interface PlanData {
  plan: string
  status: 'pending' | 'approved' | 'rejected'
}

export interface DiffData {
  text: string
  added: number
  removed: number
}

export interface InlineStep {
  id: string
  type: 'thinking' | 'tool_call'
  icon: string
  label: string
  detail?: string
  query?: string
  status: 'running' | 'completed' | 'error'
  durationMs?: number
  toolCallId?: string
  toolName?: string
  toolCategory?: string
  toolSummary?: string
  result?: string
  diff?: DiffData
  startedAt: number
}

export interface AgentStateInfo {
  current_goal: string
  status: string
  tools_used: number
  error?: string
}

export interface ProgressInfo {
  current: number
  total: number
  label: string
}

export interface ToolInfo {
  id: string
  name: string
  description: string
  enabled: boolean
}

export interface Skill {
  name: string
  description: string
}

export interface McpCatalogEnvVar {
  key: string
  label: string
  secret: boolean
  optional: boolean
  default: string
}

export interface McpCatalogEntry {
  id: string
  display_name: string
  description: string
  command: string
  args: string[]
  transport: string
  tags: string[]
  category: string
  capabilities: string[]
  env_vars: McpCatalogEnvVar[]
  homepage: string
}

export interface McpServerTool {
  name: string
  description: string
}

export interface McpServerStatus {
  status: 'ok' | 'error' | 'disconnected'
  tools: McpServerTool[]
}

export interface McpStatusResponse {
  [serverName: string]: McpServerStatus
}

export interface AgentTaskFile {
  name: string
  content: string
}

export interface AgentTaskCreate {
  name: string
  description?: string
  instructions?: string
  files?: AgentTaskFile[]
  location: string
}

export interface AgentConfig {
  model?: string
  system_prompt?: string
  max_steps?: number
  temperature?: number
}

export interface TaskData {
  id: string
  description: string
  prompt: string
  agent_type: string
  status: string
  created: string
  result: string
  error: string
  goal?: string
  mode?: string
}

export interface BackgroundRunInfo {
  run_id: string
  goal: string
  status: string
  created: string
  ended: string
}

export interface BackgroundRunLog {
  log_type: 'tool_call' | 'tool_result'
  tool?: string
  args?: Record<string, unknown>
  result?: string
  call_id?: string
}

export interface ScheduledTaskInfo {
  id: string
  goal: string
  description: string
  interval_minutes: number
  next_run: string
  enabled: boolean
  created: string
  last_run: string
}

export interface McpServerDetail {
  name: string
  status: 'ok' | 'error' | 'disconnected' | 'connecting'
  description?: string
  capabilities: string[]
  tools: McpServerTool[]
  config: {
    command: string
    args: string[]
    env: Record<string, string | { configured: boolean; masked: boolean }>
  }
  diagnostics: {
    transport: string
    startup_time_ms: number | null
    last_connected: string | null
    last_ping: string | null
    response_time_ms: number | null
  }
  permissions?: Record<string, boolean>
  source: 'catalog' | 'custom'
  category?: string
  homepage?: string
}
