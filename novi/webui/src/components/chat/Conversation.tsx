// Conversation.tsx
import { useEffect, useRef, useState } from 'react'
import { Conversation as ConversationType, Attachment, InlineStep, PlanData, AgentStateInfo, ProgressInfo, Project, BackgroundRunInfo, TimelineEntry } from '@/types'
import { ConnectionState, type MemoryActivityState } from '@/services/novi'
import type { SectionId } from '@/components/settings/SettingsModal'
import { UserMessage } from './UserMessage'
import { AssistantResponse, AssistantWorkingIndicator } from './AssistantResponse'
import { AssistantArtifacts } from './AssistantArtifacts'
import { ThinkingTrace } from './ThinkingTrace'
import { InlinePlanApproval } from './InlinePlanApproval'
import { PermissionPrompt } from '@/components/common/PermissionPrompt'
import { ActivityPanel } from './ActivityPanel'
import { ProjectContextBar } from './ProjectContextBar'
import { PromptInput } from './PromptInput'
import { LandingPage } from './LandingPage'

interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
  id: string
  timeoutMs?: number
  expiresAt?: string
}

interface Props {
  conversation: ConversationType
  connection: ConnectionState
  generating: boolean
  busyReason?: string | null
  inlineSteps: InlineStep[]
  thinking: boolean
  liveThought: string
  plan: PlanData | null
  permission: PermissionRequest | null
  agentState: AgentStateInfo | null
  progress: ProgressInfo | null
  activeProject: Project | null
  backgroundRuns: BackgroundRunInfo[]
  onSend: (content: string, attachments?: Attachment[], deepResearch?: boolean) => void
  onAttachFolder?: (path: string) => boolean
  onStop: () => void
  deepResearch?: boolean
  onToggleDeepResearch?: () => void
  onApprovePlan: () => void
  onRejectPlan: () => void
  onAnswerPermission: (allowed: boolean, requestId?: string) => void
  onOpenSettings?: (section: SectionId) => void
  workingActivityTitle?: string | null
  conversations?: ConversationType[]
  onOpenConversation?: (id: string) => void
  timeline?: TimelineEntry[]
  activityOpen?: boolean
  onToggleActivity?: () => void
  memoryActivity?: MemoryActivityState | null
}

export function Conversation({
  conversation,
  connection,
  generating,
  busyReason,
  inlineSteps,
  thinking,
  liveThought,
  plan,
  permission,
  agentState,
  progress,
  activeProject,
  backgroundRuns,
  onSend,
  onAttachFolder,
  onStop,
  deepResearch,
  onToggleDeepResearch,
  onApprovePlan,
  onRejectPlan,
  onAnswerPermission,
  onOpenSettings,
  workingActivityTitle,
  conversations,
  onOpenConversation,
  timeline,
  activityOpen: controlledActivityOpen,
  onToggleActivity: controlledToggle,
  memoryActivity,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [suggestionText, setSuggestionText] = useState('')
  const [internalActivityOpen, setInternalActivityOpen] = useState(() => {
    try {
      const current = localStorage.getItem('novi_activity_panel')
      if (current !== null) return current === 'true'
      const legacy = localStorage.getItem('cozmo_activity_panel')
      if (legacy !== null) {
        localStorage.setItem('novi_activity_panel', legacy)
        localStorage.removeItem('cozmo_activity_panel')
        return legacy === 'true'
      }
    } catch {}
    return false
  })

  const activityOpen = controlledActivityOpen ?? internalActivityOpen
  const toggleActivity = controlledToggle ?? (() => {
    const next = !internalActivityOpen
    setInternalActivityOpen(next)
    try { localStorage.setItem('novi_activity_panel', String(next)) } catch {}
  })

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [conversation.messages])

  const hasStreamingAnswer = conversation.messages.some(m => m.role === 'assistant' && m.streaming)
  const isEmpty = conversation.messages.length === 0

  // Single composer instance — placed inline (centered, under the greeting)
  // while the chat is empty, or in the pinned footer once a conversation
  // exists. Never rendered twice.
  const composer = (
    <>
      {busyReason && (
        <div className="mb-2 text-[11px] text-base-500 px-1">{busyReason}</div>
      )}
      <PromptInput
        generating={generating}
        disabled={connection !== 'open' || !!busyReason}
        onSend={(content, attachments) => { setSuggestionText(''); onSend(content, attachments, deepResearch) }}
        onAttachFolder={onAttachFolder}
        onStop={onStop}
        onOpenSettings={onOpenSettings}
        suggestion={suggestionText}
        deepResearch={!!deepResearch}
        onToggleDeepResearch={onToggleDeepResearch}
      />
    </>
  )

  return (
    <div className="flex-1 flex min-w-0">
    <main className="flex-1 flex flex-col min-w-0 bg-base-950">
      <ProjectContextBar project={activeProject} />

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty ? (
          // Centered "talking to Novi" moment: greeting → composer → quiet
          // context. Composer lives here, not in the footer, while empty.
          <div className="min-h-full flex flex-col justify-center px-6 py-8">
            <div className="max-w-3xl mx-auto w-full">
              <LandingPage
                onSuggestion={setSuggestionText}
                conversations={conversations}
                backgroundRuns={backgroundRuns}
                generating={generating}
                generatingElsewhereTitle={workingActivityTitle}
                onOpenConversation={onOpenConversation}
                timeline={timeline}
                composer={composer}
                onOpenSettings={() => onOpenSettings?.('models')}
              />
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
            {conversation.messages.map((m, i, arr) => {
              const isLast = i === arr.length - 1
              const isStreamingAssistant = m.role === 'assistant' && m.streaming
              // Pending generation: no assistant token yet, show working state below last user message
              const showPending = m.role === 'user' && (i === arr.length - 1 || i === arr.length - 2) && generating && !hasStreamingAnswer

              return (
                <div key={m.id} className="min-w-0">
                  {m.role === 'user' ? (
                    <UserMessage message={m} />
                  ) : (
                    <AssistantResponse message={m}>
                      {isLast && isStreamingAssistant && (plan || permission) && (
                        <AssistantArtifacts
                          plan={plan}
                          permission={permission}
                          onApprovePlan={onApprovePlan}
                          onRejectPlan={onRejectPlan}
                          onAnswerPermission={(allowed, id) => onAnswerPermission(allowed, id)}
                          onCancel={onStop}
                        />
                      )}
                    </AssistantResponse>
                  )}
                  {showPending && (
                    <div className="mt-3 space-y-3">
                      {thinking ? (
                        <ThinkingTrace text={liveThought} />
                      ) : (
                        <AssistantWorkingIndicator />
                      )}
                      {plan && (
                        <InlinePlanApproval plan={plan} onApprove={onApprovePlan} onReject={onRejectPlan} />
                      )}
                      {permission && (
                        <PermissionPrompt request={permission} onAnswer={(allowed) => onAnswerPermission(allowed, permission.id)} onCancel={onStop} />
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Footer composer only once a conversation has started */}
      {!isEmpty && (
        <div className="border-t border-base-800/20 bg-base-950/60 backdrop-blur-sm px-6 py-3">
          <div className="max-w-3xl mx-auto">
            {composer}
          </div>
        </div>
      )}
    </main>
      <ActivityPanel
        open={activityOpen}
        onToggle={toggleActivity}
        generating={generating}
        inlineSteps={inlineSteps}
        agentState={agentState}
        progress={progress}
        activeProject={activeProject}
        memoryActivity={memoryActivity ?? null}
      />
    </div>
  )
}
