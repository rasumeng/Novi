// Persistent assistant dashboard. Every section is derived from real runtime
// state passed in (or, for notifications, read from the notification center) —
// no fake/fabricated status. Serves as the landing view for an empty chat.

import { useMemo, useEffect, useState } from 'react'
import { Sparkles, MessageSquareText, Loader2, MoveRight, Clock, FolderKanban, Brain, History } from 'lucide-react'
import { ConnectionState } from '@/services/novi'
import { Conversation as ConversationType, BackgroundRunInfo, TimelineEntry, KnowledgeOverview as KnowledgeOverviewData } from '@/types'
import { fetchKnowledgeOverview } from '@/services/novi'
import { CONNECTION_LABEL } from './connectionStatus'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import { EmptyState } from '@/components/common/EmptyState'
import { timelineTime } from '@/utils/timeline'

interface Props {
  onSuggestion?: (text: string) => void
  connection: ConnectionState
  conversations?: ConversationType[]
  backgroundRuns?: BackgroundRunInfo[]
  generating?: boolean
  /** Title of whichever conversation owns the current generation, or null. */
  generatingElsewhereTitle?: string | null
  onOpenConversation?: (id: string) => void
  /** Assistant timeline feed for the recent-activity summary. */
  timeline?: TimelineEntry[]
}

interface SuggestionItem {
  icon: React.ElementType
  label: string
  prompt: string
}

const SUGGESTIONS: SuggestionItem[] = [
  { icon: Sparkles, label: 'Research a topic', prompt: 'Research the topic of ' },
  { icon: MessageSquareText, label: 'Draft an email', prompt: 'Draft an email about ' },
  { icon: Loader2, label: 'Explain code', prompt: 'Explain this code: ' },
  { icon: Sparkles, label: 'Brainstorm ideas', prompt: 'Help me brainstorm ' },
]

export function LandingPage({
  onSuggestion,
  connection,
  conversations = [],
  backgroundRuns = [],
  generating = false,
  generatingElsewhereTitle,
  onOpenConversation,
  timeline = [],
}: Props) {
  const status = CONNECTION_LABEL[connection]
  const { notifications } = useNotificationCenter()
  const [knowledge, setKnowledge] = useState<KnowledgeOverviewData | null>(null)

  // Real data only: the knowledge preview reflects actual learned items.
  useEffect(() => {
    let alive = true
    fetchKnowledgeOverview()
      .then((data) => alive && setKnowledge(data))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  // Only real data: conversations that have content, newest first.
  const recents = useMemo(() =>
    conversations
      .filter((c) => !c.pinned && c.messages.length > 0)
      .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
      .slice(0, 4),
    [conversations]
  )

  // Newest-first persisted activity (already merged/deduped by the hook).
  const recentActivity = useMemo(() => timeline.slice(0, 5), [timeline])

  const knowledgePreview = useMemo(() => {
    if (!knowledge || knowledge.total === 0) return []
    return knowledge.categories
      .slice(0, 3)
      .map((cat) => ({ label: cat.label, items: cat.entries.slice(0, 2) }))
  }, [knowledge])

  const activeRuns = backgroundRuns.filter((r) =>
    r.status === 'running' || r.status === 'paused' || r.status === 'pending'
  )

  const recentNotifs = notifications.slice(0, 3)

  const busy = generating || !!generatingElsewhereTitle || activeRuns.length > 0
  const busyText = generating
    ? 'Novi is responding in this conversation.'
    : generatingElsewhereTitle
      ? `Novi is working in "${generatingElsewhereTitle}".`
      : activeRuns.length > 0
        ? `${activeRuns.length} background job${activeRuns.length !== 1 ? 's' : ''} running.`
        : null

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-3xl mx-auto">
        <div className="mb-8">
          <h1 className="text-xl font-semibold text-base-100">What would you like to do?</h1>
          <div className="flex items-center gap-2 mt-2 text-[11px] text-base-500">
            <span className={`flex items-center gap-1.5`}>
              <span className={`w-1.5 h-1.5 rounded-full ${status.dot}`} />
              {status.text}
            </span>
            {busy && busyText && (
              <span className="flex items-center gap-1.5 text-accent">
                <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                {busyText}
              </span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 max-w-md mb-10">
          {SUGGESTIONS.map((s) => {
            const SI = s.icon
            return (
              <button
                key={s.label}
                onClick={() => onSuggestion?.(s.prompt)}
                className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-base-800/40 border border-base-700/60 hover:border-accent/30 text-base-400 hover:text-base-200 text-xs font-medium transition-all text-left"
              >
                <div className="w-7 h-7 rounded-lg bg-accent/10 text-accent border border-accent/20 flex items-center justify-center shrink-0">
                  <SI size={14} />
                </div>
                {s.label}
              </button>
            )
          })}
        </div>

        <div className="space-y-8">
          <section aria-label="Current activity">
            <h2 className="font-medium text-sm text-base-100 mb-2.5">Current activity</h2>
            {!busy ? (
              <EmptyState
                compact
                icon={Clock}
                title="Nothing running"
                description="Novi is idle — start a conversation or a background job."
              />
            ) : (
              <div className="space-y-2">
                {busyText && (
                  <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-base-900 border border-base-800 text-[13px] text-base-200">
                    <span className="w-2 h-2 rounded-full bg-accent animate-pulse shrink-0" />
                    {busyText}
                  </div>
                )}
                {activeRuns.map((r) => (
                  <div key={r.run_id} className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-base-900 border border-base-800 text-[12px] text-base-300">
                    <Loader2 size={13} className="text-accent animate-spin shrink-0" />
                    <span className="truncate flex-1">{r.goal || 'Background job'}</span>
                    <span className="text-base-500 capitalize shrink-0">{r.status}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="font-medium text-sm text-base-100 mb-2.5">Continue recent work</h2>
            {recents.length === 0 ? (
              <EmptyState
                compact
                icon={MessageSquareText}
                title="No previous conversations"
                description="Start a conversation — finished chats will appear here to pick back up."
              />
            ) : (
              <div className="space-y-1.5">
                {recents.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => onOpenConversation?.(c.id)}
                    className="group w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-base-900 border border-base-800 hover:border-base-700 text-left transition-colors"
                  >
                    <MessageSquareText size={14} className="text-base-500 shrink-0" />
                    <span className="text-[13px] text-base-200 truncate flex-1 group-hover:text-base-100">
                      {c.title || 'Untitled conversation'}
                    </span>
                    <MoveRight size={13} className="text-base-600 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                ))}
              </div>
            )}
          </section>

          <section aria-label="Recent activity">
            <h2 className="font-medium text-sm text-base-100 mb-2.5">Recent activity</h2>
            {recentActivity.length === 0 ? (
              <EmptyState
                compact
                icon={History}
                title="No activity yet"
                description="Conversations, memories, and learned knowledge will appear here."
              />
            ) : (
              <div className="space-y-1.5">
                {recentActivity.map((entry) => (
                  <div key={entry.id} className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-base-900 border border-base-800">
                    <Brain size={13} className="text-accent shrink-0 mt-0.5" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[12px] font-medium text-base-100 leading-snug">{entry.title}</p>
                      <p className="text-[11px] text-base-500 leading-snug mt-0.5 truncate">{entry.detail}</p>
                    </div>
                    <span className="text-[10px] text-base-500 shrink-0">{timelineTime(entry.timestamp)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section aria-label="What Novi knows">
            <h2 className="font-medium text-sm text-base-100 mb-2.5">What Novi knows</h2>
            {knowledgePreview.length === 0 ? (
              <EmptyState
                compact
                icon={Brain}
                title="Still learning"
                description="Novi will build an understanding of your work as you talk."
              />
            ) : (
              <div className="space-y-3">
                {knowledgePreview.map((cat) => (
                  <div key={cat.label} className="rounded-lg bg-base-900 border border-base-800 px-3 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-base-400 mb-1">{cat.label}</p>
                    <ul className="space-y-1">
                      {cat.items.map((item, i) => (
                        <li key={i} className="flex items-start gap-1.5 text-[12px] text-base-300">
                          <span className="text-accent/70 mt-0.5 shrink-0">•</span>
                          <span className="truncate">{item.content}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="font-medium text-sm text-base-100 mb-2.5">Recent notifications</h2>
            {recentNotifs.length === 0 ? (
              <EmptyState
                compact
                icon={FolderKanban}
                title="Nothing here yet"
                description="Job and response activity will show up here."
              />
            ) : (
              <div className="space-y-1.5">
                {recentNotifs.map((n) => (
                  <div key={n.id} className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-base-900 border border-base-800">
                    <span
                      className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${
                        n.severity === 'error' ? 'bg-err' :
                        n.severity === 'success' ? 'bg-ok' :
                        n.severity === 'attention' || n.severity === 'warning' ? 'bg-warn' :
                        'bg-accent'
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-[12px] font-medium text-base-100 leading-snug">{n.title}</p>
                      <p className="text-[11px] text-base-500 leading-snug mt-0.5">{n.message}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}