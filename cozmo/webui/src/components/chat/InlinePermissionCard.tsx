import { motion } from 'framer-motion'
import { ShieldAlert, Check, X } from 'lucide-react'

interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
}

interface Props {
  request: PermissionRequest
  onAnswer: (allowed: boolean) => void
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

function summarize(request: PermissionRequest): string {
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

export function InlinePermissionCard({ request, onAnswer }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-amber-500/30 bg-amber-500/5 overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-amber-500/10">
        <ShieldAlert size={14} className="text-amber-400 shrink-0" />
        <span className="text-[13px] font-medium text-amber-300">Permission required</span>
      </div>
      <div className="px-3 py-2.5 text-[13px] text-base-200">
        Cozmo wants to: <span className="font-medium text-base-100">{summarize(request)}</span>
      </div>
      {request.tool && (
        <div className="px-3 pb-1">
          <div className="rounded-lg bg-base-850 border border-base-800 px-2.5 py-1.5">
            <p className="text-[10px] font-mono text-accent mb-1">{request.tool}</p>
            <pre className="text-[10px] text-base-400 whitespace-pre-wrap break-all max-h-24 overflow-y-auto">
              {JSON.stringify(request.args, null, 2)}
            </pre>
          </div>
        </div>
      )}
      <div className="flex gap-2 px-3 pb-3 mt-1">
        <button
          onClick={() => onAnswer(true)}
          className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 py-2 text-[13px] font-medium text-emerald-300 transition-colors"
        >
          <Check size={14} />
          Approve
        </button>
        <button
          onClick={() => onAnswer(false)}
          className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 py-2 text-[13px] font-medium text-red-400 transition-colors"
        >
          <X size={14} />
          Deny
        </button>
      </div>
    </motion.div>
  )
}