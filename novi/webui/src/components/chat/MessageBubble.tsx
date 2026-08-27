import { motion } from 'framer-motion'
import clsx from 'clsx'
import { useState } from 'react'
import { FileText, Brain, ChevronDown, ChevronRight } from 'lucide-react'
import { ChatMessage } from '@/types'
import { ModelBadge } from '@/components/common/ModelBadge'
import { MessageContent } from './MessageContent'

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatThoughtDuration(ms?: number): string {
  if (!ms) return '...'
  return `${(ms / 1000).toFixed(2)} seconds`
}

function ThoughtBlock({ message }: { message: ChatMessage }) {
  const [open, setOpen] = useState(false)
  const peek = message.thought ? message.thought.split('\n').find((l) => l.trim())?.slice(0, 88) : ''
  return (
    <div className="w-full max-w-[85%]">
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

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={clsx('flex flex-col gap-1.5', isUser ? 'items-end' : 'items-start')}
    >
      {message.streaming && (
        <div className="flex items-center gap-1.5 px-1 pb-1">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" />
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.3s' }} />
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.6s' }} />
        </div>
      )}
      {!isUser && message.thought && <ThoughtBlock message={message} />}
      <div
        className={clsx(
          'rounded-2xl px-4 py-3 text-[15px] leading-relaxed max-w-[85%] overflow-hidden',
          isUser
            ? 'bg-accent text-white'
            : 'bg-base-850 text-base-100 shadow-panel'
        )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words leading-relaxed">{message.content}</p>
          ) : (
            <MessageContent content={message.content} streaming={message.streaming} />
          )}
          {message.attachments?.map(att => (
            <div key={att.id} className="mt-2 first:mt-0">
              {att.type === 'image' ? (
                <a href={att.url} target="_blank" rel="noreferrer">
                  <img
                    src={att.thumbnail || att.url}
                    alt={att.name}
                    className="max-w-xs rounded-lg border border-base-700 cursor-pointer hover:opacity-90 transition-opacity"
                  />
                </a>
              ) : (
                <a
                  href={att.url}
                  download={att.name}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-base-800 text-base-200 hover:bg-base-700 transition-colors text-sm"
                >
                  <FileText size={14} />
                  <span className="truncate max-w-[200px]">{att.name}</span>
                  <span className="text-base-500">{formatFileSize(att.size)}</span>
                </a>
              )}
            </div>
          ))}
        </div>
      <span className="text-[11px] text-base-500 px-1 flex items-center gap-1.5">
        {!isUser && message.model && <ModelBadge model={message.model} />}
        {message.createdAt}
      </span>
    </motion.div>
  )
}
