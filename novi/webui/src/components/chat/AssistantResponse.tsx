import { motion } from 'framer-motion'
import { useState } from 'react'
import { Brain, ChevronDown, ChevronRight } from 'lucide-react'
import clsx from 'clsx'
import { ChatMessage } from '@/types'
import { ModelBadge } from '@/components/common/ModelBadge'
import { AssistantContent } from './AssistantContent'

function formatThoughtDuration(ms?: number): string {
  if (!ms) return '...'
  return `${(ms / 1000).toFixed(2)} seconds`
}

function ThoughtBlock({ message }: { message: ChatMessage }) {
  const [open, setOpen] = useState(false)
  const peek = message.thought ? message.thought.split('\n').find((l) => l.trim())?.slice(0, 88) : ''
  return (
    <div className="w-full">
      <button
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className={clsx(
          'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] transition-colors focus-visible:ring-2 focus-visible:ring-accent/20',
          open ? 'bg-base-850 text-base-200' : 'text-base-500 hover:text-base-300 hover:bg-base-850/60'
        )}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Brain size={12} className={message.streaming ? 'text-accent animate-pulse' : 'text-base-500'} />
        <span className="font-medium">{open ? `Thought for ${formatThoughtDuration(message.thoughtElapsedMs)} — hide` : `Thought for ${formatThoughtDuration(message.thoughtElapsedMs)} — show`}</span>
      </button>
      {!open && peek && (
        <p className="ml-6 mt-1 text-[11px] leading-relaxed text-base-500 truncate max-w-[90%]">{peek}</p>
      )}
      {open && (
        <div className="mt-1.5 rounded-xl border border-base-800 bg-base-900/60 px-3.5 py-2.5 max-h-72 overflow-y-auto">
          <pre className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-base-400 font-sans">
            {message.thought}
          </pre>
        </div>
      )}
    </div>
  )
}

export function AssistantResponse({
  message,
  children,
}: {
  message: ChatMessage
  children?: React.ReactNode
}) {
  const hasContent = message.content.trim().length > 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col gap-1.5 items-start w-full min-w-0"
      data-testid="assistant-response"
    >
      {message.thought && <ThoughtBlock message={message} />}

      {hasContent ? (
        <div className="w-full min-w-0 max-w-full">
          <AssistantContent content={message.content} streaming={message.streaming} />
          {message.streaming && (
            <span className="inline-block w-2 h-4 ml-0.5 -mb-0.5 bg-accent/70 animate-pulse align-text-bottom" aria-hidden="true" />
          )}
        </div>
      ) : message.streaming ? (
        <span className="inline-block w-2 h-4 bg-accent/70 animate-pulse" aria-hidden="true" />
      ) : null}

      {children}

      <span className="text-[11px] text-base-500 px-0.5 flex items-center gap-1.5 mt-1">
        {message.model && <ModelBadge model={message.model} />}
        {message.createdAt}
      </span>
    </motion.div>
  )
}

/** Compact pre-token indicator, aligned to assistant column, no bubble. */
export function AssistantWorkingIndicator({ thinkingText }: { thinkingText?: string }) {
  if (thinkingText) {
    // Caller should render ThinkingTrace instead; this is fallback for non-thinking
    return null
  }
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex items-center gap-1.5 py-1"
      data-testid="assistant-working"
      aria-label="Novi is working"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" />
      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.2s' }} />
      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.4s' }} />
      <span className="text-[12px] text-base-500 ml-1">Novi is working</span>
    </motion.div>
  )
}
