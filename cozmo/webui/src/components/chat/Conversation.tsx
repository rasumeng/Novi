import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Conversation as ConversationType, Attachment, InlineStep, PlanData, AgentStateInfo, ProgressInfo, Project, BackgroundRunInfo, TimelineEntry } from '@/types'
import { ConnectionState } from '@/services/cozmo'
import type { SectionId } from '@/components/settings/SettingsModal'
import { MessageBubble } from './MessageBubble'
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
}

interface Props {
  conversation: ConversationType
  connection: ConnectionState
  generating: boolean
  busyReason?: string | null
  inlineSteps: InlineStep[]
  /** True while the model is streaming a reasoning trace before the answer. */
  thinking: boolean
  /** Live accumulated reasoning trace shown while `thinking`. */
  liveThought: string
  plan: PlanData | null
  permission: PermissionRequest | null
  agentState: AgentStateInfo | null
  progress: ProgressInfo | null
  activeProject: Project | null
  backgroundRuns: BackgroundRunInfo[]
  onSend: (content: string, attachments?: Attachment[], deepResearch?: boolean) => void
  onStop: () => void
  /** Deep Research mode for the active conversation (explicit user mode). */
  deepResearch?: boolean
  onToggleDeepResearch?: () => void
  onApprovePlan: () => void
  onRejectPlan: () => void
  onAnswerPermission: (allowed: boolean, requestId?: string) => void
  onOpenSettings?: (section: SectionId) => void
  /** Title of whichever conversation owns the current generation, or null when idle. */
  workingActivityTitle?: string | null
  /** Full conversation list for the landing dashboard's "continue" section. */
  conversations?: ConversationType[]
  /** Open another conversation from the landing page. */
  onOpenConversation?: (id: string) => void
  /** Assistant timeline feed for the landing summary. */
  timeline?: TimelineEntry[]
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
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [suggestionText, setSuggestionText] = useState('')
  const [activityOpen, setActivityOpen] = useState(() => {
    try { return localStorage.getItem('cozmo_activity_panel') === 'true' } catch { return false }
  })

  const toggleActivity = () => {
    const next = !activityOpen
    setActivityOpen(next)
    try { localStorage.setItem('cozmo_activity_panel', String(next)) } catch {}
  }

  // stick to bottom as tokens stream in
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [conversation.messages])

  const hasStreamingAnswer = conversation.messages.some(m => m.role === 'assistant' && m.streaming)

  return (
    <div className="flex-1 flex min-w-0">
    <main className="flex-1 flex flex-col min-w-0 bg-base-950">
      <ProjectContextBar project={activeProject} />

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
          {conversation.messages.length === 0 ? (
            <LandingPage
              onSuggestion={setSuggestionText}
              connection={connection}
              conversations={conversations}
              backgroundRuns={backgroundRuns}
              generating={generating}
              generatingElsewhereTitle={workingActivityTitle}
              onOpenConversation={onOpenConversation}
              timeline={timeline}
            />
          ) : (
            conversation.messages.map((m, i, arr) => (
              <div key={m.id}>
                <MessageBubble message={m} />
                {m.role === 'user' && (i === arr.length - 1 || i === arr.length - 2) && generating && !hasStreamingAnswer && (
                  <div className="mt-3">
                    {thinking ? (
                      <ThinkingTrace text={liveThought} />
                    ) : (
                      <motion.div
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="flex items-center gap-1 px-1"
                      >
                        <span className="w-1 h-1 rounded-full bg-accent/70 animate-glow" />
                        <span className="w-1 h-1 rounded-full bg-accent/70 animate-glow" style={{ animationDelay: '0.2s' }} />
                        <span className="w-1 h-1 rounded-full bg-accent/70 animate-glow" style={{ animationDelay: '0.4s' }} />
                      </motion.div>
                    )}
                    {plan && (
                      <div className="mt-3">
                        <InlinePlanApproval plan={plan} onApprove={onApprovePlan} onReject={onRejectPlan} />
                      </div>
                    )}
                    {permission && (
                      <div className="mt-3">
                        <PermissionPrompt request={permission} onAnswer={(allowed) => onAnswerPermission(allowed, permission.id)} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      <div className=" border-base-800 bg-base-950/80 backdrop-blur px-6 py-4">
        <div className="max-w-3xl mx-auto">
          {busyReason && (
            <div className="mb-2 text-[11px] text-base-500 px-1">{busyReason}</div>
          )}
          <PromptInput
            generating={generating}
            disabled={connection !== 'open' || !!busyReason}
            onSend={(content, attachments) => { setSuggestionText(''); onSend(content, attachments, deepResearch) }}
            onStop={onStop}
            onOpenSettings={onOpenSettings}
            suggestion={suggestionText}
            deepResearch={!!deepResearch}
            onToggleDeepResearch={onToggleDeepResearch}
          />
        </div>
      </div>
    </main>
      <ActivityPanel
        open={activityOpen}
        onToggle={toggleActivity}
        generating={generating}
        inlineSteps={inlineSteps}
        agentState={agentState}
        progress={progress}
        activeProject={activeProject}
      />
    </div>
  )
}
