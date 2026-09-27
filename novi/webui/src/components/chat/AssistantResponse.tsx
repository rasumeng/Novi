import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { ChatMessage } from '@/types'
import type { ProjectedTool } from '@/state/runReducer'
import { MessageContent } from './MessageContent'

/** Reasoning and answer share one Markdown renderer, without an assistant bubble. */
export function AssistantResponse({ message }: { message: ChatMessage }) {
  const thinking = !!message.streaming && !!message.thought && !message.content
  return (
    <div className="min-w-0 space-y-3" data-testid="assistant-response">
      {message.thought && <ReasoningTrace content={message.thought} thinking={thinking} />}
      {message.content && <MessageContent content={message.content} streaming={message.streaming} />}
    </div>
  )
}

function ReasoningTrace({ content, thinking }: { content: string; thinking: boolean }) {
  const [open, setOpen] = useState(thinking)

  // Show reasoning as it streams, then tuck it away when the answer starts.
  useEffect(() => setOpen(thinking), [thinking])

  return (
    <div className="text-sm text-base-500" data-testid="reasoning">
      <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}
        className="inline-flex cursor-pointer items-center gap-1 py-1 text-base-400 hover:text-base-300 focus-visible:outline-accent"
        role={thinking ? 'status' : undefined}>
        <span>{thinking ? 'Thinking' : 'Thoughts'}</span>
        <ChevronRight size={14} aria-hidden="true"
          className={`transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="mt-1 text-base-500">
          <MessageContent content={content} tone="muted" />
        </div>
      )}
    </div>
  )
}

/** Visible only until reasoning, text, or tool activity arrives. */
export function WaitingBubble() {
  return (
    <div className="inline-flex gap-1.5 rounded-full bg-base-800 px-4 py-3"
      role="status" aria-label="Waiting for Novi" data-testid="waiting-bubble">
      {[0, 1, 2].map(index => (
        <span key={index} aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full bg-base-400 motion-safe:animate-pulse"
          style={{ animationDelay: `${index * 180}ms` }} />
      ))}
    </div>
  )
}

/** Tool activity stays in reading order, without an activity card. */
export function ToolTrace({ tool }: { tool: ProjectedTool }) {
  const running = ['requested', 'running'].includes(tool.status)
  const label = `${running ? 'Running' : tool.status === 'succeeded' ? 'Ran' : tool.status.replace(/_/g, ' ')} ${tool.name.replace(/_/g, ' ')}`
  return (
    <details className="group text-sm text-base-400" data-testid="tool-trace">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 py-1 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
        <span>{label}{running ? '…' : ''}</span>
        <ChevronRight size={14} aria-hidden="true"
          className="transition-transform group-open:rotate-90" />
      </summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">
        {JSON.stringify(tool.arguments, null, 2)}
      </pre>
      {tool.result && <div className="mt-2"><MessageContent content={tool.result} tone="muted" /></div>}
    </details>
  )
}
