export type SectionId = 'general' | 'models' | 'agent' | 'memory' | 'skills' | 'connectors' | 'permissions' | 'developer'

export interface ToolInfo {
  id: string
  name: string
  description: string
  enabled: boolean
}

export interface McpServerConfig {
  command: string
  args?: string[]
  env?: Record<string, string>
}

export interface RuntimeConfig {
  max_history?: number
  max_steps?: number
  max_tool_output_chars?: number
  memory_distance_threshold?: number
  max_memory_results?: number
  max_project_results?: number
  temperatures?: Record<string, number>
  tool_gate?: Record<string, string[]>
}

export interface AgentProfile {
  description?: string
  model?: string | null
  tools?: string[]
}

export interface AgentConfig {
  primary?: string[]
  profiles?: Record<string, AgentProfile>
  [key: string]: unknown
}

export interface LlmConfig {
  max_tokens?: number
  workloads?: Record<string, { model?: string }>
}

export interface SettingsData {
  llm?: LlmConfig
  models: Record<string, unknown>
  ollama?: { url?: string }
  providers?: { default?: string; ollama?: { url?: string; reasoning?: boolean }; openai?: { api_key_env?: string } }
  runtime?: RuntimeConfig
  permissions?: Record<string, unknown>
  mcp?: { servers: Record<string, McpServerConfig> }
  memory?: { max_turns_before_summary?: number; max_short_term_pairs?: number }
  embedding?: { model?: string }
  personality?: string
  agent?: AgentConfig
  workspace?: { path?: string; knowledge?: string; git_repo?: string }
  search?: { url?: string; backend?: string }
  code?: { index_extensions?: string[] }
  desktop?: { enabled?: boolean }
  telegram?: { enabled?: boolean; bot_token?: string; allowed_chat_ids?: number[] }
  [key: string]: unknown
}
