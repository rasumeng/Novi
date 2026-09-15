import { useEffect, useRef, useState } from 'react'
import { Brain, Check, Circle, Pause, Play, RefreshCw } from 'lucide-react'
import { API_BASE, type MemoryActivityState } from '@/services/novi'
import { useToast } from '@/hooks/useToast'

type HistoryEntry = { id: string; state: string; mode: string; error: string; changes: Array<{ action: string; text: string; before: string | null; evidence: string[] }> }
const stages = [['proposing', 'Reading the conversation'], ['verifying', 'Checking for consistency'], ['applying', 'Updating memory']] as const

export function MemoryActivity({ activity }: { activity: MemoryActivityState | null }) {
  const { showInfo, showError } = useToast()
  const announced = useRef(new Set<string>())
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const busy = !!activity && ['proposing', 'verifying', 'applying'].includes(activity.state)
  const label = activity?.mode === 'shadow' ? 'Reviewing memory suggestions — no changes saved' : 'Novi is updating its memory'

  useEffect(() => {
    if (!activity?.job_id || !busy) return
    const key = `${activity.instance_id}:${activity.job_id}`
    if (!announced.current.has(key)) { announced.current.add(key); showInfo(label) }
  }, [activity, busy, label, showInfo])

  async function loadHistory() {
    setLoading(true)
    try {
      const response = await fetch(`${API_BASE}/api/memory/activity/history`)
      if (!response.ok) throw new Error('Could not load memory activity')
      setHistory(await response.json())
    } catch (error) { showError(String(error)) }
    finally { setLoading(false) }
  }
  useEffect(() => { void loadHistory() }, [activity?.version])

  async function pause() {
    try {
      const response = await fetch(`${API_BASE}/api/memory/pause`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paused: activity?.state !== 'paused' }) })
      if (!response.ok) throw new Error('Could not change memory pause state')
    } catch (error) { showError(String(error)) }
  }

  const status = !activity ? 'Waiting for activity' : busy ? 'Consolidating' : activity.state === 'applied'
    ? `Updated ${activity.note_ids.length} ${activity.note_ids.length === 1 ? 'memory' : 'memories'}` : activity.state === 'paused'
      ? 'Consolidation paused' : activity.state === 'disabled' ? 'Automatic saving is off' : activity.state === 'unavailable'
        ? 'Automatic saving unavailable' : activity.state === 'parked' ? 'No safe change found' : activity.state === 'abstained' ? 'No changes needed' : activity.state === 'idle' ? 'Up to date' : 'Memory unchanged'

  return <div className="flex-1 overflow-y-auto px-4 py-4">
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2 text-sm font-medium text-base-100">
          <span className={`grid h-7 w-7 place-items-center rounded-full ${busy ? 'bg-accent/15 text-accent' : 'bg-base-850 text-base-400'}`}><Brain size={14} /></span>
          <span role="status" aria-live="polite">{status}</span>
        </div>
        {activity?.reason && !busy && <p className="mt-2 pl-9 text-xs leading-relaxed text-base-500">{activity.reason}</p>}
      </div>
      {(busy || activity?.state === 'paused') && <button type="button" onClick={pause} className="flex items-center gap-1 rounded-md border border-base-700 px-2 py-1 text-[11px] text-base-300 hover:border-base-600 hover:text-base-100">
        {activity?.state === 'paused' ? <Play size={11} /> : <Pause size={11} />}{activity?.state === 'paused' ? 'Resume' : 'Pause'}
      </button>}
    </div>

    {activity?.job_id && <div className="mt-5 border-l border-base-700/70 pl-4">
      {stages.map(([key, text], index) => {
        const current = stages.findIndex(([stage]) => stage === activity.state)
        const complete = current > index || activity.state === 'applied'
        const active = key === activity.state
        return <div key={key} className="relative pb-4 last:pb-0">
          <span className="absolute -left-[21px] top-0.5 bg-base-950 text-base-600">{complete ? <Check size={12} className="text-ok" /> : <Circle size={12} className={active ? 'fill-accent text-accent animate-pulse' : ''} />}</span>
          <p className={`text-xs ${active ? 'text-base-100' : complete ? 'text-base-400' : 'text-base-600'}`}>{text}</p>
        </div>
      })}
    </div>}

    <div className="mt-6 flex items-center justify-between border-b border-base-800/50 pb-2">
      <h3 className="text-xs font-medium text-base-300">Recent consolidations</h3>
      <button type="button" onClick={() => void loadHistory()} aria-label="Refresh memory activity" className="rounded p-1 text-base-500 hover:text-base-200"><RefreshCw size={12} className={loading ? 'animate-spin' : ''} /></button>
    </div>
    {!loading && history.length === 0 && <p className="py-6 text-center text-xs text-base-600">No consolidations recorded yet.</p>}
    <div className="divide-y divide-base-800/40">{history.map(entry => <details key={entry.id} className="group py-3">
      <summary className="cursor-pointer list-none text-xs text-base-300"><span className="flex items-center justify-between gap-2"><span>{entry.mode === 'shadow' ? 'Suggestion review' : 'Memory update'}</span><span className="text-base-600 group-open:text-base-400">{entry.state}</span></span></summary>
      {entry.error && <p className="mt-2 text-xs text-danger">{entry.error}</p>}
      {entry.changes.length === 0 && <p className="mt-2 text-xs text-base-500">No changes saved.</p>}
      {entry.changes.map((change, index) => <div key={index} className="mt-3 text-xs leading-relaxed">
        {change.before && <p className="text-base-500 line-through">{change.before}</p>}<p className="mt-1 whitespace-pre-wrap text-base-200">{change.text}</p>
        {change.evidence.length > 0 && <details className="mt-2 text-base-500"><summary className="cursor-pointer">Source evidence</summary>{change.evidence.map((quote, i) => <blockquote key={i} className="mt-1 border-l border-base-700 pl-2">{quote}</blockquote>)}</details>}
      </div>)}
    </details>)}</div>
  </div>
}
