import { useState, useEffect, useCallback } from 'react'
import { PlayCircle, Square, RefreshCw, Clock, CheckCircle, XCircle, PauseCircle, Play, Loader2 } from 'lucide-react'
import { BackgroundRunInfo } from '@/types'
import { EmptyState } from '@/components/common/EmptyState'

interface Props {
  runs: BackgroundRunInfo[]
  onStart: (goal: string) => void
  onStop: (runId: string) => void
  onRefresh: () => void
}

const STATUS_ICONS: Record<string, React.ElementType> = {
  running: PlayCircle,
  paused: PauseCircle,
  completed: CheckCircle,
  done: CheckCircle,
  error: XCircle,
  cancelled: XCircle,
}

const STATUS_COLORS: Record<string, string> = {
  running: 'text-accent',
  paused: 'text-amber-400',
  completed: 'text-emerald-400',
  done: 'text-emerald-400',
  error: 'text-red-400',
  cancelled: 'text-base-500',
}

const STATUS_BG: Record<string, string> = {
  running: 'bg-accent/5 border-accent/20',
  paused: 'bg-amber-500/5 border-amber-500/20',
  completed: 'bg-emerald-500/5 border-emerald-500/20',
  done: 'bg-emerald-500/5 border-emerald-500/20',
  error: 'bg-red-500/5 border-red-500/20',
  cancelled: 'bg-base-800/50 border-base-700/50',
}

function formatTime(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return iso }
}

function elapsed(created: string, ended: string): string {
  if (!created) return ''
  const start = new Date(created).getTime()
  const end = ended ? new Date(ended).getTime() : Date.now()
  const ms = end - start
  if (ms < 1000) return 'just now'
  if (ms < 60000) return `${Math.floor(ms / 1000)}s`
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
  return `${Math.floor(ms / 3600000)}h ${Math.round((ms % 3600000) / 60000)}m`
}

function isActive(status: string): boolean {
  return status === 'running' || status === 'paused' || status === 'pending'
}

export function JobsPage({ runs, onStart, onStop, onRefresh }: Props) {
  const [goal, setGoal] = useState('')
  const [showNew, setShowNew] = useState(false)

  const active = runs.filter(r => isActive(r.status))
  const completed = runs.filter(r => !isActive(r.status))

  const handleSubmit = useCallback(() => {
    if (!goal.trim()) return
    onStart(goal.trim())
    setGoal('')
    setShowNew(false)
  }, [goal, onStart])

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-base-950">
      <header className="h-14 shrink-0 flex items-center justify-between px-5 border-b border-base-800">
        <h2 className="text-sm font-medium text-base-100">Jobs</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={onRefresh}
            aria-label="Refresh jobs"
            className="p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={15} />
          </button>
          <button
            onClick={() => setShowNew(v => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
          >
            <Play size={13} />
            New Job
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {showNew && (
          <div className="mb-6 p-4 rounded-xl border border-base-700 bg-base-900">
            <form onSubmit={(e) => { e.preventDefault(); handleSubmit() }} className="space-y-3">
              <input
                autoFocus
                value={goal}
                onChange={e => setGoal(e.target.value)}
                placeholder="Describe what Novi should do..."
                className="w-full px-3 py-2 rounded-lg bg-base-800 border border-base-700 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent"
              />
              <div className="flex justify-end gap-2">
                <button type="button" onClick={() => setShowNew(false)} className="px-3 py-1.5 rounded-lg text-xs text-base-400 hover:bg-base-800 transition-colors">Cancel</button>
                <button type="submit" disabled={!goal.trim()} className="px-4 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-accent/90 disabled:opacity-40 transition-colors">Start Job</button>
              </div>
            </form>
          </div>
        )}

        {active.length > 0 && (
          <section className="mb-8">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-base-500 mb-3">Active ({active.length})</h3>
            <div className="space-y-2">
              {active.map(r => (
                <JobCard key={r.run_id} run={r} onStop={onStop} />
              ))}
            </div>
          </section>
        )}

        {completed.length > 0 && (
          <section>
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-base-500 mb-3">Completed ({completed.length})</h3>
            <div className="space-y-2">
              {completed.map(r => (
                <JobCard key={r.run_id} run={r} onStop={onStop} />
              ))}
            </div>
          </section>
        )}

        {runs.length === 0 && (
          <EmptyState
            icon={Loader2}
            title="No jobs yet"
            description="Start a job to run long-running tasks in the background."
            action={
              <button
                onClick={() => setShowNew(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
              >
                <Play size={13} />
                Start a job
              </button>
            }
          />
        )}
      </div>
    </div>
  )
}

function JobCard({ run, onStop }: { run: BackgroundRunInfo; onStop: (id: string) => void }) {
  const StatusIcon = STATUS_ICONS[run.status] ?? PlayCircle
  const isActiveStatus = isActive(run.status)

  return (
    <div className={`rounded-xl border px-4 py-3 transition-colors ${STATUS_BG[run.status] ?? 'bg-base-900 border-base-800'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <StatusIcon size={16} className={`shrink-0 mt-0.5 ${STATUS_COLORS[run.status] ?? 'text-base-400'}`} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-base-100 truncate">{run.goal || 'Untitled job'}</p>
            <div className="flex items-center gap-3 mt-1 text-[11px] text-base-500">
              {run.created && (
                <span className="flex items-center gap-1">
                  <Clock size={10} />
                  {formatTime(run.created)}
                </span>
              )}
              <span className="flex items-center gap-1">
                {elapsed(run.created, run.ended)}
              </span>
              <span className={`capitalize ${STATUS_COLORS[run.status] ?? 'text-base-400'}`}>
                {run.status}
              </span>
            </div>
          </div>
        </div>
        {isActiveStatus && (
          <button
            onClick={() => onStop(run.run_id)}
            aria-label={`Stop job: ${run.goal || 'untitled'}`}
            className="shrink-0 p-1.5 rounded-lg text-base-400 hover:text-red-400 hover:bg-base-800 transition-colors"
            title="Stop"
          >
            <Square size={13} />
          </button>
        )}
      </div>
    </div>
  )
}