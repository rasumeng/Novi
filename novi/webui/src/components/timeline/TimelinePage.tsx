import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { MessageSquare, Brain, Sparkles, History, RefreshCw, MoveRight } from 'lucide-react'
import { TimelineEntry } from '@/types'
import { EmptyState } from '@/components/common/EmptyState'
import { mergeTimeline, groupByDay, timelineTime } from '@/utils/timeline'

interface Props {
  entries: TimelineEntry[]
  /** Re-hydrate history from the backend. */
  onRefresh: () => void
  /** Open a referenced conversation (same pattern as notifications). */
  onOpenConversation?: (id: string) => void
}

const KIND_META: Record<string, { icon: React.ElementType; color: string; iconBg: string }> = {
  'conversation.observed': { icon: MessageSquare, color: 'text-accent', iconBg: 'bg-accent/10 border-accent/20' },
  'knowledge.extracted': { icon: Brain, color: 'text-emerald-400', iconBg: 'bg-emerald-500/10 border-emerald-500/20' },
  'knowledge.promoted': { icon: Sparkles, color: 'text-amber-400', iconBg: 'bg-amber-500/10 border-amber-500/20' },
}

function metaFor(kind: string) {
  return KIND_META[kind] ?? KIND_META['knowledge.extracted']
}

export function TimelinePage({ entries, onRefresh, onOpenConversation }: Props) {
  const sorted = useMemo(() => mergeTimeline(entries), [entries])
  const groups = useMemo(() => groupByDay(sorted), [sorted])

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-base-950">
      <header className="h-14 shrink-0 flex items-center justify-between px-5 border-b border-base-800">
        <h2 className="text-sm font-medium text-base-100">Timeline</h2>
        <button
          onClick={onRefresh}
          aria-label="Refresh timeline"
          title="Refresh"
          className="p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
        >
          <RefreshCw size={15} />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {sorted.length === 0 ? (
          <EmptyState
            icon={History}
            title="No activity yet"
            description="Novi's activity — conversations, memories, and learned knowledge — will appear here as you work together."
          />
        ) : (
          <div className="max-w-2xl mx-auto space-y-8">
            {groups.map((group) => (
              <section key={group.day} aria-label={group.label}>
                <h3 className="text-[11px] font-semibold uppercase tracking-wider text-base-500 mb-3">
                  {group.label}
                </h3>
                <div className="space-y-1.5">
                  {group.entries.map((entry) => (
                    <TimelineRow key={entry.id} entry={entry} onOpenConversation={onOpenConversation} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function TimelineRow({ entry, onOpenConversation }: { entry: TimelineEntry; onOpenConversation?: (id: string) => void }) {
  const meta = metaFor(entry.kind)
  const Icon = meta.icon
  const conversationId = entry.conversation_id
  const clickable = !!conversationId && !!onOpenConversation

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? `${entry.title}. ${entry.detail}. Open conversation.` : undefined}
      onClick={() => {
        if (clickable && conversationId) onOpenConversation!(conversationId)
      }}
      onKeyDown={(e) => {
        if (clickable && conversationId && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onOpenConversation!(conversationId)
        }
      }}
      className={`flex items-start gap-3 rounded-xl border border-base-700/50 bg-base-900/50 px-3.5 py-3 transition-colors ${
        clickable ? 'cursor-pointer hover:bg-base-800/50' : ''
      }`}
    >
      <div className={`mt-0.5 shrink-0 w-8 h-8 rounded-lg border flex items-center justify-center ${meta.iconBg}`}>
        <Icon size={14} className={meta.color} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[13px] font-medium text-base-100 truncate">{entry.title}</p>
          <span className="text-[10px] text-base-500 shrink-0">{timelineTime(entry.timestamp)}</span>
        </div>
        <p className="text-[12px] text-base-400 leading-snug mt-0.5 whitespace-pre-wrap break-words">
          {entry.detail}
        </p>
      </div>
      {clickable && <MoveRight size={12} className="text-base-600 shrink-0 mt-1" aria-hidden="true" />}
    </motion.div>
  )
}