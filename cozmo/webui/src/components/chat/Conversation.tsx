import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Sparkles } from 'lucide-react'
import { Conversation as ConversationType, Attachment, InlineStep, PlanData, AgentStateInfo, ProgressInfo, Project, BackgroundRunInfo } from '@/types'
import { ConnectionState } from '@/services/cozmo'
import type { SectionId } from '@/components/settings/SettingsModal'
import { MessageBubble } from './MessageBubble'
import { InlineTraceTimeline } from './InlineTraceTimeline'
import { InlinePermissionCard } from './InlinePermissionCard'
import { ActivityPanel } from './ActivityPanel'
import { ProjectContextBar } from './ProjectContextBar'
import { NotificationBell } from './NotificationBell'
import { PromptInput } from './PromptInput'
import { LandingPage } from './LandingPage'
import { CONNECTION_LABEL } from './connectionStatus'

interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
}

interface Props {
  conversation: ConversationType
  connection: ConnectionState
  generating: boolean
  busyReason?: string | null
  inlineSteps: InlineStep[]
  plan: PlanData | null
  permission: PermissionRequest | null
  agentState: AgentStateInfo | null
  progress: ProgressInfo | null
  activeProject: Project | null
  backgroundRuns: BackgroundRunInfo[]
  onSend: (content: string, attachments?: Attachment[]) => void
  onStop: () => void
  onApprovePlan: () => void
  onRejectPlan: () => void
  onAnswerPermission: (allowed: boolean) => void
  onOpenSettings?: (section: SectionId) => void
}

export function Conversation({
  conversation,
  connection,
  generating,
  busyReason,
  inlineSteps,
  plan,
  permission,
  agentState,
  progress,
  activeProject,
  backgroundRuns,
  onSend,
  onStop,
  onApprovePlan,
  onRejectPlan,
  onAnswerPermission,
  onOpenSettings,
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

  const conn = CONNECTION_LABEL[connection]

  return (
    <div className="flex-1 flex min-w-0">
    <main className="flex-1 flex flex-col min-w-0 bg-base-950">
      <header className="h-14 shrink-0 flex items-center justify-between px-5 border-b border-base-800">
        <div className="flex items-center gap-2.5 text-sm text-base-300">
          <div className="w-5 h-5 rounded-md bg-accent/15 flex items-center justify-center">
            <Sparkles size={12} className="text-accent" />
          </div>
          <span className="text-base-100 font-medium">{conversation.title}</span>
        </div>
        <div className="flex items-center gap-1">
          <NotificationBell/>
          <div className="flex items-center gap-1.5 text-[11px] text-base-500 ml-1">
            <span className={`w-1.5 h-1.5 rounded-full ${conn.dot}`} />
            {conn.text}
          </div>
        </div>
      </header>

      <ProjectContextBar project={activeProject} />

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
          {conversation.messages.length === 0 ? (
            <LandingPage onSuggestion={setSuggestionText} connection={connection} />
          ) : (
            conversation.messages.map((m, i, arr) => (
              <div key={m.id}>
                <MessageBubble message={m} />
                {m.role === 'user' && (i === arr.length - 1 || i === arr.length - 2) && (generating || inlineSteps.length > 0) && (
                  <div className="mt-3">
                    {inlineSteps.length === 0 ? (
                      <motion.div
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="flex items-center gap-2 px-1"
                      >
                        <div className="flex gap-1">
                          <span className="w-2 h-2 rounded-full bg-accent/70 animate-glow" />
                          <span className="w-2 h-2 rounded-full bg-accent/70 animate-glow" style={{ animationDelay: '0.2s' }} />
                          <span className="w-2 h-2 rounded-full bg-accent/70 animate-glow" style={{ animationDelay: '0.4s' }} />
                        </div>
                        <span className="text-[12px] text-base-500">Thinking...</span>
                      </motion.div>
                    ) : (
                      <InlineTraceTimeline
                        steps={inlineSteps}
                        plan={plan}
                        onApprovePlan={onApprovePlan}
                        onRejectPlan={onRejectPlan}
                        generating={generating}
                      />
                    )}
                    {permission && (
                      <div className="mt-3">
                        <InlinePermissionCard request={permission} onAnswer={onAnswerPermission} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      <div className="border-t border-base-800 bg-base-950/80 backdrop-blur px-6 py-4">
        <div className="max-w-3xl mx-auto">
          {busyReason && (
            <div className="mb-2 text-[11px] text-base-500 px-1">{busyReason}</div>
          )}
          <PromptInput
            generating={generating}
            disabled={connection !== 'open' || !!busyReason}
            onSend={(content, attachments) => { setSuggestionText(''); onSend(content, attachments) }}
            onStop={onStop}
            onOpenSettings={onOpenSettings}
            suggestion={suggestionText}
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
