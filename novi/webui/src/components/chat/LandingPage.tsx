// LandingPage.tsx
import { useMemo, useEffect, useState, type ReactNode } from 'react'
import { MessageSquareText, MoveRight } from 'lucide-react'
import { Conversation as ConversationType, BackgroundRunInfo, TimelineEntry, KnowledgeOverview as KnowledgeOverviewData } from '@/types'
import { fetchKnowledgeOverview } from '@/services/novi'
import { fetchSystemHealth, type SystemHealth } from '@/components/settings/api'
import { NoviMascot } from '@/components/brand/NoviMascot'
import { getNoviGreeting } from '@/utils/noviGreeting'

interface Props {
  onSuggestion?: (text: string) => void
  conversations?: ConversationType[]
  backgroundRuns?: BackgroundRunInfo[]
  generating?: boolean
  generatingElsewhereTitle?: string | null
  onOpenConversation?: (id: string) => void
  timeline?: TimelineEntry[]
  /** The composer, rendered by the parent so there's only ever one instance. */
  composer?: ReactNode
  onOpenSettings?: () => void
}

interface SuggestionItem {
  label: string
  prompt: string
}

const SUGGESTIONS: SuggestionItem[] = [
  { label: 'help me plan something', prompt: 'Help me plan ' },
  { label: 'remember something for me', prompt: 'Remember that ' },
  { label: 'look into something', prompt: 'Research ' },
]

export function LandingPage({
  onSuggestion,
  conversations = [],
  backgroundRuns = [],
  generating = false,
  generatingElsewhereTitle,
  onOpenConversation,
  composer,
  onOpenSettings,
}: Props) {
  const [knowledge, setKnowledge] = useState<KnowledgeOverviewData | null>(null)
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const greeting = useMemo(() => getNoviGreeting(), [])

  useEffect(() => {
    let alive = true
    fetchKnowledgeOverview()
      .then((data) => alive && setKnowledge(data))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    let alive = true
    let requestId = 0
    const refreshHealth = async () => {
      const currentRequest = ++requestId
      const result = await fetchSystemHealth()
      if (alive && currentRequest === requestId && result) setHealth(result)
    }
    void refreshHealth()
    window.addEventListener('novi:readiness-changed', refreshHealth)
    window.addEventListener('focus', refreshHealth)
    return () => {
      alive = false
      window.removeEventListener('novi:readiness-changed', refreshHealth)
      window.removeEventListener('focus', refreshHealth)
    }
  }, [])

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
    ? 'working here'
    : generatingElsewhereTitle
      ? `working in "${generatingElsewhereTitle}"`
      : activeRuns.length > 0
        ? `${activeRuns.length} background job${activeRuns.length !== 1 ? 's' : ''} running`
        : null

  const hasContext = recents.length > 0 || knowledgePreview.length > 0

  return (
    <div className="flex flex-col items-center">
      {/* Mascot + greeting — compact conversational row, no card/border */}
      <div className="flex items-center gap-3 ">
        <div className="shrink-0">
          <NoviMascot expression="happy" size={80} />
        </div>
        <p className="text-[30px] font-semibold text-base-100 leading-snug">
          {greeting}
        </p>
      </div>

      {health && !health.ready && (
        <div className="mt-4 flex w-full max-w-xl items-start justify-between gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/5 p-4">
          <div>
            <p className="text-sm font-medium text-base-100">Finish setting up Novi</p>
            <p className="mt-1 text-xs leading-relaxed text-base-400">Choose a local model and make sure memory embeddings are ready before starting your first chat.</p>
          </div>
          {onOpenSettings && <button onClick={onOpenSettings} className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90">Set up</button>}
        </div>
      )}

      {/* busy indicator — live activity state, not static product copy */}
      {busy && busyText && (
        <div className="flex items-center gap-1.5 mb-6 text-[11px] text-accent">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          {busyText}
        </div>
      )}

      {/* Conversational suggestions */}
      <div className="flex flex-wrap justify-center gap-2 mb-6">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.label}
            onClick={() => onSuggestion?.(s.prompt)}
            className="px-3.5 py-2 rounded-full bg-base-800/40 border border-base-700/40 hover:border-accent/30 hover:bg-base-800/70 text-base-300 hover:text-base-100 text-[12px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-accent/20"
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Composer — primary interaction, directly below greeting/suggestions */}
      {composer && <div className="w-full mb-8">{composer}</div>}

      {/* Context, kept quiet, below the composer */}
      {hasContext && (
        <div className="w-full space-y-3">
          {recents.length > 0 && (
            <div>
              <p className="text-[10px] font-medium tracking-wide uppercase text-base-600 mb-1.5 text-center">
                Picking back up
              </p>
              <div className="space-y-1">
                {recents.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => onOpenConversation?.(c.id)}
                    className="group w-full flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-base-900/60 text-left transition-colors focus-visible:ring-2 focus-visible:ring-accent/20"
                  >
                    <MessageSquareText size={12} className="text-base-600 shrink-0" />
                    <span className="text-[12.5px] text-base-400 truncate flex-1 group-hover:text-base-200">
                      {c.title || 'Untitled conversation'}
                    </span>
                    <MoveRight size={11} className="text-base-700 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                ))}
              </div>
            </div>
          )}

          {knowledgePreview.length > 0 && (
            <div>
              <p className="text-[10px] font-medium tracking-wide uppercase text-base-600 mb-1.5 text-center">
                Things I remember
              </p>
              <div className="flex flex-wrap justify-center gap-1.5">
                {knowledgePreview.flatMap((cat) =>
                  cat.items.map((item, i) => (
                    <span
                      key={`${cat.label}-${i}`}
                      className="px-2.5 py-1 rounded-full bg-base-900/60 border border-base-800 text-[11px] text-base-400 truncate max-w-[220px]"
                    >
                      {item.content}
                    </span>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
