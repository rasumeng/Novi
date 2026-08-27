// Persistent assistant dashboard. Every section is derived from real runtime
// state passed in (or, for notifications, read from the notification center) —
// no fake/fabricated status. Serves as the landing view for an empty chat.

import { useMemo, useEffect, useState } from 'react'
import { Sparkles, MessageSquareText, MoveRight, Brain, Compass, Lightbulb, Search } from 'lucide-react'
import { ConnectionState } from '@/services/novi'
import { Conversation as ConversationType, BackgroundRunInfo, TimelineEntry, KnowledgeOverview as KnowledgeOverviewData } from '@/types'
import { fetchKnowledgeOverview } from '@/services/novi'
import { CONNECTION_LABEL } from './connectionStatus'
import { EmptyState } from '@/components/common/EmptyState'

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
  { icon: Compass, label: 'Plan with Novi', prompt: 'Help me plan ' },
  { icon: Lightbulb, label: 'Ask Novi to remember this', prompt: 'Remember that ' },
  { icon: Search, label: 'Let Novi research it', prompt: 'Research ' },
]

export function LandingPage({
  onSuggestion,
  connection,
  conversations = [],
  backgroundRuns = [],
  generating = false,
  generatingElsewhereTitle,
  onOpenConversation,
}: Props) {
  const status = CONNECTION_LABEL[connection]
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

  // Only real data: conversations that have content, newest first — capped at 3 for calm.
  const recents = useMemo(() =>
    conversations
      .filter((c) => !c.pinned && c.messages.length > 0)
      .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
      .slice(0, 3),
    [conversations]
  )

  const knowledgePreview = useMemo(() => {
    if (!knowledge || knowledge.total === 0) return []
    return knowledge.categories
      .slice(0, 2)
      .map((cat) => ({ label: cat.label, items: cat.entries.slice(0, 2) }))
  }, [knowledge])

  const activeRuns = backgroundRuns.filter((r) =>
    r.status === 'running' || r.status === 'paused' || r.status === 'pending'
  )

  const busy = generating || !!generatingElsewhereTitle || activeRuns.length > 0
  const busyText = generating
    ? 'Novi is working here'
    : generatingElsewhereTitle
      ? `Novi is working in "${generatingElsewhereTitle}"`
      : activeRuns.length > 0
        ? `${activeRuns.length} background job${activeRuns.length !== 1 ? 's' : ''} running`
        : null

  return (
    <div className="flex-1 overflow-y-auto px-6 py-8">
      <div className="max-w-3xl mx-auto">
        {/* Thesis framing — route • remember • act, input stays dominant via Conversation.tsx */}
        <div className="mb-6">
          <p className="text-[10px] font-semibold tracking-[0.14em] uppercase text-base-500">Route • Remember • Act</p>
          <h1 className="text-[22px] font-semibold tracking-tight text-base-100 mt-1 leading-tight">Novi is ready to work with you</h1>
          <div className="flex items-center gap-2 mt-2 text-[11px] text-base-500">
            <span className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${status.dot}`} />
              {status.text}
            </span>
            <span className="text-base-700">·</span>
            <span className="text-base-500">Local agent • memory on • tools connected</span>
            {busy && busyText && (
              <span className="hidden sm:inline-flex items-center gap-1.5 text-accent ml-1">
                <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                {busyText}
              </span>
            )}
          </div>
          {busy && busyText && (
            <span className="sm:hidden inline-flex items-center gap-1.5 text-[11px] text-accent mt-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              {busyText}
            </span>
          )}
          <div className="mt-4 h-px bg-accent/10" aria-hidden="true" />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 mb-8">
          {SUGGESTIONS.map((s) => {
            const SI = s.icon
            return (
              <button
                key={s.label}
                onClick={() => onSuggestion?.(s.prompt)}
                className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-base-800/30 border border-base-700/50 hover:border-accent/25 hover:bg-base-800/60 text-base-300 hover:text-base-100 text-xs font-medium transition-colors text-left focus-visible:ring-2 focus-visible:ring-accent/20"
              >
                <span className="w-8 h-8 rounded-xl bg-accent/10 text-accent border border-accent/15 flex items-center justify-center shrink-0">
                  <SI size={15} />
                </span>
                {s.label}
              </button>
            )
          })}
        </div>

        {/* Only three quiet sections — input remains dominant, these support */}
        <div className="space-y-7">
          {busy && busyText && (
            <section aria-label="Novi is working">
              <h2 className="text-[11px] font-semibold tracking-widest uppercase text-base-500 mb-2">Working</h2>
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-base-900 border border-base-800 text-[13px] text-base-200">
                <span className="w-2 h-2 rounded-full bg-accent animate-pulse shrink-0" />
                {busyText}
              </div>
            </section>
          )}

          <section>
            <h2 className="text-[11px] font-semibold tracking-widest uppercase text-base-500 mb-2">Continue where you left off</h2>
            {recents.length === 0 ? (
              <p className="text-xs text-base-500 py-2">No previous work yet — start a conversation and it will appear here.</p>
            ) : (
              <div className="space-y-1.5">
                {recents.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => onOpenConversation?.(c.id)}
                    className="group w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl bg-base-900 border border-base-800 hover:border-base-700 text-left transition-colors focus-visible:ring-2 focus-visible:ring-accent/20"
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

          <section aria-label="What Novi noticed">
            <h2 className="text-[11px] font-semibold tracking-widest uppercase text-base-500 mb-2">What Novi noticed</h2>
            {knowledgePreview.length === 0 ? (
              <p className="text-xs text-base-500 py-2">Novi will notice preferences and facts as you chat — they will appear here.</p>
            ) : (
              <div className="space-y-2.5">
                {knowledgePreview.map((cat) => (
                  <div key={cat.label} className="rounded-xl bg-base-900 border border-base-800 px-3 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-base-400 mb-1">{cat.label}</p>
                    <ul className="space-y-1">
                      {cat.items.map((item, i) => (
                        <li key={i} className="flex items-start gap-1.5 text-[12px] text-base-300">
                          <span className="text-accent/60 mt-0.5 shrink-0">—</span>
                          <span className="truncate">{item.content}</span>
                        </li>
                      ))}
                    </ul>
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