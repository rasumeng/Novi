import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { PanelRightClose, PanelRightOpen, Activity, Target, Clock, FolderKanban, Cpu, Layers, Loader2, Brain } from 'lucide-react'
import { InlineStep, AgentStateInfo, ProgressInfo, Project } from '@/types'
import type { MemoryActivityState } from '@/services/novi'
import { EmptyState } from '@/components/common/EmptyState'
import { MemoryActivity } from './MemoryActivity'

interface Props {
  open: boolean
  onToggle: () => void
  generating: boolean
  inlineSteps: InlineStep[]
  agentState: AgentStateInfo | null
  progress: ProgressInfo | null
  activeProject: Project | null
  memoryActivity: MemoryActivityState | null
}

const STATUS_LABELS: Record<string, string> = {
  running: 'Working',
  thinking: 'Thinking',
  planning: 'Planning',
  executing: 'Executing',
  waiting: 'Waiting for approval',
  idle: 'Idle',
  done: 'Completed',
  error: 'Error',
}

function currentStepLabel(steps: InlineStep[]): string | null {
  const running = steps.find(s => s.status === 'running')
  if (running) return running.label
  const last = steps[steps.length - 1]
  if (last) return last.label
  return null
}

function elapsed(steps: InlineStep[]): string {
  const total = steps.reduce((sum, s) => sum + (s.durationMs ?? 0), 0)
  if (total === 0) {
    const running = steps.find(s => s.status === 'running')
    if (running) {
      const ms = Date.now() - running.startedAt
      return `${(ms / 1000).toFixed(0)}s`
    }
    return ''
  }
  if (total < 1000) return `${total}ms`
  if (total < 60000) return `${(total / 1000).toFixed(1)}s`
  return `${Math.floor(total / 60000)}m ${Math.round((total % 60000) / 1000)}s`
}

export function ActivityPanel({
  open,
  onToggle,
  generating,
  inlineSteps,
  agentState,
  progress,
  activeProject,
  memoryActivity,
}: Props) {
  const hasActivity = generating || inlineSteps.length > 0 || agentState !== null || progress !== null
  const memoryBusy = !!memoryActivity && ['proposing', 'verifying', 'applying'].includes(memoryActivity.state)
  const [tab, setTab] = useState<'activity' | 'memory'>(memoryBusy ? 'memory' : 'activity')
  const [width, setWidth] = useState(() => {
    try { return Math.min(520, Math.max(280, Number(localStorage.getItem('novi_inspector_width')) || 320)) } catch { return 320 }
  })
  useEffect(() => { if (memoryBusy) setTab('memory') }, [memoryBusy])

  function beginResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = width
    let finalWidth = startWidth
    const move = (next: PointerEvent) => {
      finalWidth = Math.min(520, Math.max(280, startWidth + startX - next.clientX))
      setWidth(finalWidth)
    }
    const end = () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', end)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      try { localStorage.setItem('novi_inspector_width', String(Math.round(finalWidth))) } catch {}
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', end)
  }

  return (
    <>
      <button
        onClick={onToggle}
        aria-label={open ? 'Hide activity' : 'Show activity'}
        aria-expanded={open}
        className="hidden shrink-0 w-7 flex items-center justify-center border-l border-base-800/30 text-base-400 hover:text-base-100 hover:bg-base-850/50 transition-colors"
        title={open ? 'Hide activity' : 'Show activity'}
      >
        {open ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
      </button>

      <AnimatePresence>
        {open && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="relative border-l border-base-800/30 bg-base-950 overflow-hidden shrink-0"
          >
            <div
              role="separator"
              tabIndex={0}
              aria-label="Resize side panel"
              aria-orientation="vertical"
              aria-valuemin={280}
              aria-valuemax={520}
              aria-valuenow={Math.round(width)}
              onPointerDown={beginResize}
              onKeyDown={(event) => {
                if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
                const next = Math.min(520, Math.max(280, width + (event.key === 'ArrowLeft' ? 16 : -16)))
                setWidth(next)
                try { localStorage.setItem('novi_inspector_width', String(next)) } catch {}
              }}
              className="absolute inset-y-0 left-0 z-10 w-1 cursor-col-resize hover:bg-accent/35 focus:bg-accent/35"
            />
            <div style={{ width }} className="h-full flex flex-col">
              <div role="tablist" aria-label="Side panel" className="flex h-11 shrink-0 items-end gap-1 border-b border-base-800/30 px-3">
                <Tab active={tab === 'activity'} onClick={() => setTab('activity')} icon={Activity} label="Activity" />
                <Tab active={tab === 'memory'} onClick={() => setTab('memory')} icon={Brain} label="Memory" />
                {(generating || memoryBusy) && (
                  <span className="flex gap-0.5">
                    <span className="w-1 h-1 rounded-full bg-accent animate-glow" />
                    <span className="w-1 h-1 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.2s' }} />
                    <span className="w-1 h-1 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.4s' }} />
                  </span>
                )}
              </div>

              {tab === 'memory' ? <MemoryActivity activity={memoryActivity} /> : <div className="flex-1 overflow-y-auto px-3 py-4 space-y-4">
                {!hasActivity && (
                  <EmptyState
                    compact
                    icon={Loader2}
                    title="No active tasks"
                    description="Start a conversation to see live tool steps and progress here."
                  />
                )}

                {agentState?.current_goal && (
                  <Section icon={Target} label="Current task">
                    <p className="text-[13px] text-base-100 leading-snug">{agentState.current_goal}</p>
                  </Section>
                )}

                {agentState && (
                  <Section icon={Activity} label="Status">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${
                        agentState.status === 'error' ? 'bg-red-400' :
                        agentState.status === 'idle' ? 'bg-base-500' :
                        agentState.status === 'done' ? 'bg-emerald-400' :
                        'bg-accent animate-pulse'
                      }`} />
                      <span className="text-[13px] text-base-200">
                        {STATUS_LABELS[agentState.status] ?? agentState.status}
                      </span>
                    </div>
                  </Section>
                )}

                {generating && inlineSteps.length === 0 && (
                  <Section icon={Activity} label="Status">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                      <span className="text-[13px] text-base-200">Thinking...</span>
                    </div>
                  </Section>
                )}

                {currentStepLabel(inlineSteps) && (
                  <Section icon={Clock} label="Current step">
                    <p className="text-[13px] text-base-100">{currentStepLabel(inlineSteps)}</p>
                    <p className="text-[11px] text-base-500 mt-0.5">{elapsed(inlineSteps)}</p>
                  </Section>
                )}

                {progress && (
                  <Section icon={Layers} label="Progress">
                    <div className="space-y-1.5">
                      <div className="flex justify-between text-[11px]">
                        <span className="text-base-300">{progress.label}</span>
                        <span className="text-base-500">{progress.current}/{progress.total}</span>
                      </div>
                      <div className="w-full h-1.5 rounded-full bg-base-800 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-accent transition-all duration-300"
                          style={{ width: `${Math.min(100, (progress.current / progress.total) * 100)}%` }}
                        />
                      </div>
                    </div>
                  </Section>
                )}

                {generating && inlineSteps.length > 0 && (
                  <Section icon={Activity} label="Running steps">
                    <div className="space-y-1">
                      {inlineSteps.slice(-5).map(s => (
                        <div key={s.id} className="flex items-center gap-2 text-[12px]">
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                            s.status === 'running' ? 'bg-accent animate-pulse' :
                            s.status === 'error' ? 'bg-red-400' :
                            'bg-emerald-400'
                          }`} />
                          <span className={`truncate ${
                            s.status === 'running' ? 'text-base-200' : 'text-base-500'
                          }`}>{s.label}</span>
                        </div>
                      ))}
                    </div>
                  </Section>
                )}

                {activeProject && (
                  <Section icon={FolderKanban} label="Project">
                    <p className="text-[13px] text-base-100">{activeProject.name}</p>
                    {activeProject.description && (
                      <p className="text-[11px] text-base-500 mt-0.5">{activeProject.description}</p>
                    )}
                  </Section>
                )}

                {agentState && agentState.tools_used > 0 && (
                  <Section icon={Cpu} label="Tools used">
                    <p className="text-[13px] text-base-100">{agentState.tools_used}</p>
                  </Section>
                )}
              </div>}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  )
}

function Section({ icon: Icon, label, children }: { icon: React.ElementType; label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1.5">
        <Icon size={12} className="text-accent shrink-0" />
        <span className="text-[10px] font-semibold uppercase tracking-wider text-base-500">{label}</span>
      </div>
      {children}
    </div>
  )
}

function Tab({ active, onClick, icon: Icon, label }: { active: boolean; onClick: () => void; icon: React.ElementType; label: string }) {
  return <button type="button" role="tab" aria-selected={active} onClick={onClick}
    className={`relative flex h-10 items-center gap-1.5 px-2 text-xs transition-colors ${active ? 'text-base-100' : 'text-base-500 hover:text-base-200'}`}>
    <Icon size={13} />{label}
    {active && <span className="absolute inset-x-2 bottom-0 h-px bg-accent" />}
  </button>
}
