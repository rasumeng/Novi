// Single permission experience. One component, two presentations (inline in the
// conversation flow, or as a modal overlay) — same approval logic, same styling,
// same terminology. Backend permission handling is untouched; this only changes
// how the request is presented and answered.
//
// Designed as an "important moment": it explains WHY Cozmo needs permission and
// WHAT will happen, shows clear Allow / Deny, and is fully keyboard-usable
// (auto-focus on a safe default, Escape to deny, arrow keys to move focus).

import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { ShieldAlert, Check, X } from 'lucide-react'

export interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
}

interface Props {
  request: PermissionRequest
  onAnswer: (allowed: boolean) => void
  /** 'inline' renders in the conversation stream; 'modal' overlays the app. */
  variant?: 'inline' | 'modal'
}

const TOOL_LABELS: Record<string, string> = {
  read: 'Read files',
  write_file: 'Write files',
  edit_file: 'Edit files',
  delete_file: 'Delete files',
  bash: 'Run command',
  grep: 'Search code',
  glob: 'Find files',
  web_fetch: 'Fetch URL',
}

/** Plain-language description of the action Cozmo wants to perform. */
function summarizeAction(request: PermissionRequest): string {
  const tool = request.tool
  const args = request.args
  const p = args['path'] as string | undefined
  const cmd = args['command'] as string | undefined
  const q = args['query'] as string | undefined
  const url = args['url'] as string | undefined

  if (tool === 'delete_file' || tool === 'delete') {
    const files = args['files'] || args['paths'] || (p ? [p] : [])
    const count = Array.isArray(files) ? files.length : 1
    return `Delete ${count} file${count !== 1 ? 's' : ''}`
  }
  if (tool === 'bash') {
    const truncated = typeof cmd === 'string' ? cmd.slice(0, 80) : '?'
    return `Run: ${truncated}${typeof cmd === 'string' && cmd.length > 80 ? '...' : ''}`
  }
  if (tool === 'write_file') return `Write to ${p ?? 'file'}`
  if (tool === 'edit_file') return `Edit ${p ?? 'file'}`
  if (tool === 'read') return `Read ${p ?? 'file'}`
  if (tool === 'web_fetch') return `Fetch ${url ?? 'page'}`
  if (tool === 'grep') return `Search code for "${q ?? '?'}"`
  if (tool === 'glob') return `Find files matching pattern`
  return (TOOL_LABELS[tool] ?? tool.replace(/_/g, ' ')) + (p ? `: ${p}` : '')
}

export function PermissionPrompt({ request, onAnswer, variant = 'inline' }: Props) {
  const denyRef = useRef<HTMLButtonElement>(null)
  const allowRef = useRef<HTMLButtonElement>(null)

  // Focus a safe default (Deny) and make Escape a deny — never an allow.
  useEffect(() => {
    denyRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onAnswer(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onAnswer])

  const action = summarizeAction(request)

  const handleArrowKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const next = e.key === 'ArrowLeft' ? denyRef.current : allowRef.current
      next?.focus()
    }
  }

  const body = (
    <div>
      <p className="text-[13px] text-base-200 leading-relaxed">
        Cozmo wants to <span className="font-medium text-base-100">{action}</span>.
      </p>
      <p className="text-[12px] text-base-500 mt-1 leading-relaxed">
        This action can change your files or system. Review it before allowing.
      </p>

      {request.tool && (
        <div className="mt-3 rounded-lg bg-base-850 border border-base-800 px-2.5 py-1.5">
          <p className="text-[10px] font-mono text-accent mb-1">{request.tool}</p>
          <pre className="text-[10px] text-base-400 whitespace-pre-wrap break-all max-h-24 overflow-y-auto">
            {JSON.stringify(request.args, null, 2)}
          </pre>
        </div>
      )}

      <div className="flex gap-2 mt-4" role="group" aria-label="Permission decision" onKeyDown={handleArrowKey}>
        <button
          ref={denyRef}
          onClick={() => onAnswer(false)}
          className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 py-2 text-[13px] font-medium text-red-400 transition-colors"
        >
          <X size={14} />
          Deny
        </button>
        <button
          ref={allowRef}
          onClick={() => onAnswer(true)}
          className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 py-2 text-[13px] font-medium text-emerald-300 transition-colors"
        >
          <Check size={14} />
          Allow
        </button>
      </div>
    </div>
  )

  if (variant === 'modal') {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="absolute inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      >
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-labelledby="permission-title"
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.15 }}
          className="w-[420px] rounded-2xl border border-base-700 bg-base-900 p-5 shadow-panel"
        >
          <div className="flex items-center gap-2.5 mb-4">
            <div className="w-8 h-8 rounded-lg bg-amber-500/15 flex items-center justify-center">
              <ShieldAlert size={16} className="text-amber-400" />
            </div>
            <div>
              <p id="permission-title" className="text-sm font-medium text-base-100">Permission required</p>
              <p className="text-[11px] text-base-500">Cozmo needs your approval to continue</p>
            </div>
          </div>
          {body}
        </motion.div>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      role="alert"
      aria-labelledby="permission-title-inline"
      className="rounded-xl border border-amber-500/30 bg-amber-500/5 overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-amber-500/10">
        <ShieldAlert size={14} className="text-amber-400 shrink-0" />
        <span id="permission-title-inline" className="text-[13px] font-medium text-amber-300">Permission required</span>
      </div>
      <div className="px-3 py-3">{body}</div>
    </motion.div>
  )
}