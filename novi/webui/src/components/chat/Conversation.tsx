// Conversation.tsx
import { useEffect, useRef, useState } from 'react'
import { Conversation as ConversationType, Attachment, PlanData, Project, BackgroundRunInfo, TimelineEntry, SourceFolder } from '@/types'
import { ConnectionState } from '@/services/novi'
import type { RunProjection } from '@/state/runReducer'
import type { SectionId } from '@/components/settings/SettingsModal'
import { UserMessage } from './UserMessage'
import { AssistantResponse, ToolTrace, WaitingBubble } from './AssistantResponse'
import { InlinePlanApproval } from './InlinePlanApproval'
import { PermissionPopup } from './PermissionPopup'
import { ProjectContextBar } from './ProjectContextBar'
import { PromptInput } from './PromptInput'
import { LandingPage } from './LandingPage'

interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
  id: string
  timeoutMs?: number
  expiresAt?: string
  effects?: string[]
  digest?: string
  proposedDiff?: unknown
}

interface Props {
  conversation: ConversationType
  connection: ConnectionState
  generating: boolean
  busyReason?: string | null
  plan: PlanData | null
  permission: PermissionRequest | null
  activeProject: Project | null
  backgroundRuns: BackgroundRunInfo[]
  onSend: (content: string, attachments?: Attachment[], deepResearch?: boolean) => void
  onAttachFolder?: (path: string) => boolean
  onAttachSource?: (convId: string, path: string) => Promise<SourceFolder>
  onDetachSource?: (path: string) => Promise<void>
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
  runProjection?: RunProjection | null
}

export function Conversation({
  conversation,
  connection,
  generating,
  busyReason,
  plan,
  permission,
  activeProject,
  backgroundRuns,
  onSend,
  onAttachFolder,
  onAttachSource,
  onDetachSource,
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
  runProjection,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [suggestionText, setSuggestionText] = useState('')

  const followBottom = useRef(true)
  useEffect(() => {
    const el = scrollRef.current
    if (el && followBottom.current) el.scrollTop = el.scrollHeight
  }, [conversation.messages, runProjection])
  useEffect(() => { followBottom.current = true }, [conversation.id])

  const currentRun = runProjection && ['queued', 'running', 'awaiting_permission'].includes(runProjection.status)
    ? runProjection : null
  const hasStarted = !!currentRun && (currentRun.toolOrder.length > 0 || !!currentRun.statusText ||
    Object.values(currentRun.messages).some(message => !!message.content || !!message.thought))
  const waiting = generating && !hasStarted && !permission
  const traces = runProjection?.toolOrder.map(id => runProjection.tools[id]) ?? []
  const lastUserId = conversation.messages.filter(message => message.role === 'user').slice(-1)[0]?.id
  const tracesAfter = (id: string | null) => traces.filter(tool => (tool.afterMessageId ?? null) === id)

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
        onAttachSource={onAttachSource}
        onDetachSource={onDetachSource}
        attachedSources={conversation.sources || []}
        conversationId={conversation.id}
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

      <div ref={scrollRef} className="flex-1 overflow-y-auto" onScroll={() => {
        const el = scrollRef.current
        if (el) followBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }}>
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
            {conversation.messages.map((m) => {
              return (
                <div key={m.id} className="min-w-0">
                  {m.role === 'user' ? (
                    <UserMessage message={m} />
                  ) : (
                    <AssistantResponse message={m} />
                  )}
                  <div className="space-y-2">
                    {(m.id === lastUserId ? tracesAfter(null) : tracesAfter(m.id)).map(tool => (
                      <ToolTrace key={tool.id} tool={tool} />
                    ))}
                  </div>
                </div>
              )
            })}
            {generating && (
              <div className="space-y-3" aria-live="polite">
                {waiting && <WaitingBubble />}
                {currentRun?.statusText && !currentRun.messageOrder.length && !traces.length && (
                  <p className="text-sm text-base-500">{currentRun.statusText}</p>
                )}
                {plan && (
                  <InlinePlanApproval plan={plan} onApprove={onApprovePlan} onReject={onRejectPlan} />
                )}
              </div>
            )}
            {permission && (
              <PermissionPopup
                request={permission}
                onAllow={() => onAnswerPermission(true, permission.id)}
                onDeny={() => onAnswerPermission(false, permission.id)}
                onCancel={onStop}
              />
            )}
            {runProjection && ['blocked', 'failed', 'cancelled', 'interrupted'].includes(runProjection.status) && (
              <div role="status" className={`rounded-lg border px-3 py-2 text-[12px] ${
                runProjection.status === 'cancelled'
                  ? 'border-base-700 bg-base-850/60 text-base-300'
                  : 'border-red-500/25 bg-red-500/5 text-red-300'
              }`}>
                <span className="font-medium capitalize">{runProjection.status.replace('_', ' ')}</span>
                {runProjection.error && <span className="text-base-500"> — {runProjection.error}</span>}
              </div>
            )}
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
    </div>
  )
}
