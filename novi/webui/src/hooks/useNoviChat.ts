import { useCallback, useEffect, useRef, useState } from 'react'
import { Conversation, InlineStep, Attachment, Project, PlanData, BackgroundRunInfo, AgentStateInfo, ProgressInfo, TimelineEntry } from '@/types'
import { NoviClient, ConnectionState, ServerEvent, saveConversation, deleteConversationApi, createProject, updateProject, deleteProjectApi, fetchProjectConversations, fetchTimeline } from '@/services/novi'
import { fetchConversationsDeduped, fetchProjectsDeduped, fetchTimelineEnvelopeDeduped, getConversationsCache, getProjectsCache, getTimelineEnvelopeCache } from '@/hooks/bootCache'
import { useToast } from '@/hooks/useToast'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import { notifyPolicy } from '@/notifications/policy'
import { notifyIfUnfocused } from '@/native/tauri'
import { mergeTimeline } from '@/utils/timeline'
import { API_BASE, type MemoryActivityState } from '@/services/novi'

export interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
  id: string
  timeoutMs?: number
  expiresAt?: string
}

// The backend agent session is single-flight: only one generation can be in
// progress at a time, and its streaming events (token/thinking/tool_call/...)
// carry no conversation id of their own. `GenerationOwner` is the frontend's
// record of *which* conversation those anonymous events belong to. Every
// handler for a streaming event must resolve its target through this owner,
// never through whatever conversation happens to be on screen — otherwise
// switching conversations mid-stream reroutes the response into the wrong one.
interface GenerationOwner {
  conversationId: string
}

let idCounter = 0
const nextId = () => `id-${Date.now()}-${idCounter++}`
const now = () =>
  new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

const DRAFT_ID = '__draft__'
const STOP_FALLBACK_MS = 8000
// Startup hydration timeout removed — fresh loading design will decide policy.

export function useNoviChat() {
  const { showError } = useToast()
  const { push: pushNotification } = useNotificationCenter()
  const clientRef = useRef<NoviClient | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [memoryActivity, setMemoryActivity] = useState<MemoryActivityState | null>(null)
  const receiveMemory = useCallback((next: MemoryActivityState) => {
    setMemoryActivity(prev => prev && prev.instance_id === next.instance_id && prev.version >= next.version ? prev : next)
  }, [])
  useEffect(() => {
    if (connection !== 'open') return
    const abort = new AbortController()
    fetch(`${API_BASE}/api/memory/activity`, { signal: abort.signal })
      .then(response => { if (!response.ok) throw new Error('Memory status unavailable'); return response.json() })
      .then(receiveMemory)
      .catch(error => { if (error.name !== 'AbortError') console.warn(error) })
    return () => abort.abort()
  }, [connection, receiveMemory])
  const [conversations, setConversations] = useState<Conversation[]>(() => getConversationsCache() ?? [])
  const [conversationsHydrated, setConversationsHydrated] = useState(() => getConversationsCache() !== null)
  const [activeId, setActiveId] = useState(() => '')

  // The single generation owner. null when nothing is in flight. This is the
  // only thing that decides where streaming events land — see module comment.
  const [owner, setOwner] = useState<GenerationOwner | null>(null)

  const [inlineSteps, setInlineSteps] = useState<InlineStep[]>([])
  // In-conversation reasoning state: `thinking` is true while the model is
  // emitting a reasoning trace before the first answer token; `liveThought`
  // holds the accumulated trace text so the conversation can render it live.
  const [thinking, setThinking] = useState(false)
  const [liveThought, setLiveThought] = useState('')
  const [permission, setPermission] = useState<PermissionRequest | null>(null)
  // Ref mirror of the pending permission prompt. `handleEvent` runs through a
  // ref (`handleEventRef`) so its closure can go stale; the ref keeps the
  // permission_timeout id-match check honest.
  const permissionRef = useRef<PermissionRequest | null>(null)
  useEffect(() => { permissionRef.current = permission }, [permission])
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [backgroundRuns, setBackgroundRuns] = useState<BackgroundRunInfo[]>([])
  const currentModelRef = useRef('')
  const stopTimeoutRef = useRef<number | null>(null)
  // Deep Research is an explicit per-conversation mode: enabling it routes
  // that conversation's messages through the research strategy/intent. It is
  // user-controlled UI state, never a hidden routing heuristic.
  const [deepResearchByConv, setDeepResearchByConv] = useState<Record<string, boolean>>({})

  const [agentState, setAgentState] = useState<AgentStateInfo | null>(null)
  const [progress, setProgress] = useState<ProgressInfo | null>(null)
  const [projects, setProjects] = useState<Project[]>(() => getProjectsCache() ?? [])
  const [activeProjectId, setActiveProjectId] = useState<string | null>(() => {
    try { return localStorage.getItem('novi_active_project_id') || null } catch { return null }
  })
  // Milestone 4: assistant timeline feed. Live entries prepend from
  // `assistant_event`; history is hydrated via REST on mount.
  // Distinguishes empty ("No knowledge yet") vs error ("Brain store unavailable").
  const cachedTimeline = getTimelineEnvelopeCache()
  const [timeline, setTimeline] = useState<TimelineEntry[]>(() => cachedTimeline?.data ?? [])
  const [timelineError, setTimelineError] = useState<string | null>(() => cachedTimeline?.error ?? null)
  const [timelineStatus, setTimelineStatus] = useState<'ok' | 'unavailable' | 'disabled'>(() => cachedTimeline?.status ?? 'ok')
  const [timelineLoading, setTimelineLoading] = useState(() => cachedTimeline === null)
  const [projectsLoading, setProjectsLoading] = useState(() => getProjectsCache() === null)
  const [projectsError, setProjectsError] = useState<string | null>(null)
  const [jobsLoading, setJobsLoading] = useState(false)
  const [jobsError, setJobsError] = useState<string | null>(null)
  const pushTimelineEntry = useCallback((entry: TimelineEntry) => {
    setTimeline(prev => mergeTimeline([entry, ...prev]))
  }, [])
  const refreshTimeline = useCallback(() => {
    setTimelineLoading(true)
    fetchTimelineEnvelopeDeduped({ force: true }).then((env) => {
      setTimelineStatus(env.status)
      setTimelineError(env.error ?? null)
      if (env.data.length) setTimeline(prev => mergeTimeline([...env.data, ...prev]))
      else if (env.status === 'ok') {
        // keep timeline as-is for empty ok — UI shows empty banner
      }
    }).catch(() => {
      setTimelineStatus('unavailable')
      setTimelineError('Brain store unavailable — check logs')
    }).finally(() => setTimelineLoading(false))
  }, [])
  const refreshProjects = useCallback(() => {
    setProjectsLoading(true)
    setProjectsError(null)
    fetchProjectsDeduped({ force: true })
      .then((list) => setProjects(list))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : "Couldn't load your projects."
        setProjectsError(msg || "Couldn't load your projects.")
        showError("Couldn't load your projects.")
      })
      .finally(() => setProjectsLoading(false))
  }, [showError])
  // Id of the conversation with unsaved changes, or null. Deliberately not a
  // boolean: persistence must save the conversation that actually changed
  // (the generation owner), not whatever is currently on screen.
  const dirtyIdRef = useRef<string | null>(null)

  const clearStopFallback = () => {
    if (stopTimeoutRef.current != null) {
      window.clearTimeout(stopTimeoutRef.current)
      stopTimeoutRef.current = null
    }
  }

  // Load conversations on mount — deduped via bootCache to avoid double fetch with useBoot
  useEffect(() => {
    let active = true
    const convCached = getConversationsCache()
    if (convCached !== null) {
      setConversations(convCached)
      setConversationsHydrated(true)
    } else {
      fetchConversationsDeduped()
        .then((list) => {
          if (active) setConversations(list)
        })
        .catch(() => {
          if (active) {
            setConversations([])
            showError("Couldn't load your conversations. Is Novi's backend running?")
          }
        })
        .finally(() => {
          if (active) setConversationsHydrated(true)
        })
    }
    const projCached = getProjectsCache()
    if (projCached !== null) {
      setProjects(projCached)
      setProjectsLoading(false)
    } else {
      setProjectsLoading(true)
      setProjectsError(null)
      fetchProjectsDeduped()
        .then((list) => {
          if (active) setProjects(list)
        })
        .catch((e: unknown) => {
          if (!active) return
          const msg = e instanceof Error ? e.message : "Couldn't load your projects."
          setProjectsError(msg || "Couldn't load your projects.")
          showError("Couldn't load your projects.")
        })
        .finally(() => {
          if (active) setProjectsLoading(false)
        })
    }
    return () => {
      active = false
    }
  }, [showError])

  useEffect(() => clearStopFallback, [])

  // persist activeProjectId
  useEffect(() => {
    try {
      if (activeProjectId) localStorage.setItem('novi_active_project_id', activeProjectId)
      else localStorage.removeItem('novi_active_project_id')
    } catch {}
  }, [activeProjectId])

  // Task 1.4 canonical: conversation.projectId is authoritative. project.conversationIds is derived on read.
  // Legacy backfill effect is now a no-op (kept as guard for old persisted data via server migration).
  useEffect(() => {}, [projects, conversations])

  // Milestone 4: hydrate the persisted assistant timeline on mount — reuse bootCache if already hydrated
  useEffect(() => {
    const env = getTimelineEnvelopeCache()
    if (env !== null) {
      setTimelineStatus(env.status)
      setTimelineError(env.error ?? null)
      if (env.data.length) setTimeline((prev) => (prev.length ? prev : mergeTimeline([...env.data, ...prev])))
      setTimelineLoading(false)
      return
    }
    refreshTimeline()
  }, [refreshTimeline])

  // Persist whichever conversation was last marked dirty (never "the active one" —
  // the active one may not be the conversation that actually changed).
  useEffect(() => {
    const id = dirtyIdRef.current
    if (!id) return
    dirtyIdRef.current = null
    const conv = conversations.find((c) => c.id === id)
    if (conv) {
      saveConversation(conv).catch(() => {
        showError("Couldn't save this conversation. Your changes may be lost if you close Novi.")
      })
    }
  })

  // Novi always opens to a fresh landing page.  Conversation history remains
  // one click away in the sidebar, but a restart never drops the user back
  // into whichever chat happened to be active last time.
  const resolvedActiveId = activeId || DRAFT_ID

  const updateConversation = useCallback(
    (id: string, fn: (c: Conversation) => Conversation) => {
      setConversations((convs) =>
        convs.map((c) => (c.id === id ? fn(c) : c))
      )
    },
    []
  )

  // Appends a token to the conversation that OWNS the current generation,
  // not to `resolvedActiveId`. If there is no owner (e.g. a stray event after
  // stop/reset), the token is dropped rather than misattributed.
  // Reasoning trace buffer. `reasoning` events stream in before the first
  // token; the accumulated text is attached to the assistant message on first
  // token (and drained again on done for token-less edge cases).
  const thoughtRef = useRef('')
  const thoughtStartedAtRef = useRef(0)

  const attachThought = useCallback((ownerId: string) => {
    const text = thoughtRef.current
    if (!text) return
    const elapsed = Date.now() - thoughtStartedAtRef.current
    thoughtRef.current = ''
    updateConversation(ownerId, (c) => {
      const msgs = [...c.messages]
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'assistant') {
        msgs[msgs.length - 1] = { ...last, thought: text, thoughtElapsedMs: Math.max(0, elapsed) }
      }
      return { ...c, messages: msgs }
    })
  }, [updateConversation])

  const appendToken = useCallback(
    (text: string) => {
      const ownerId = owner?.conversationId
      if (!ownerId) return
      // Attach the accumulated reasoning trace to the assistant message being
      // created/streamed (drain once, on the first token).
      const thought = thoughtRef.current
      const thoughtElapsed = thoughtStartedAtRef.current
        ? Math.max(0, Date.now() - thoughtStartedAtRef.current)
        : undefined
      if (thought) {
        thoughtRef.current = ''
        setThinking(false)
        setLiveThought('')
      }
      updateConversation(ownerId, (c) => {
        const msgs = [...c.messages]
        const last = msgs[msgs.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
          const base = thought ? { ...last, thought, thoughtElapsedMs: thoughtElapsed } : last
          msgs[msgs.length - 1] = { ...base, content: last.content + text }
        } else {
          msgs.push({
            id: nextId(),
            role: 'assistant',
            content: text,
            createdAt: now(),
            streaming: true,
            model: currentModelRef.current || undefined,
            ...(thought ? { thought, thoughtElapsedMs: thoughtElapsed } : {}),
          })
        }
        return { ...c, messages: msgs, updatedAt: 'Just now' }
      })
    },
    [owner, updateConversation]
  )

  const finishStreaming = useCallback(() => {
    const ownerId = owner?.conversationId
    currentModelRef.current = ''
    setThinking(false)
    setLiveThought('')
    if (!ownerId) return
    attachThought(ownerId)
    updateConversation(ownerId, (c) => ({
      ...c,
      messages: c.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    }))
    dirtyIdRef.current = ownerId
  }, [owner, updateConversation, attachThought])

  const toolLabel = (tool: string, args: Record<string, unknown>): string => {
    const p = args['path'] as string | undefined
    const q = args['query'] as string | undefined
    const pt = args['pattern'] as string | undefined
    const cmd = args['command'] as string | undefined
    const url = args['url'] as string | undefined
    switch (tool) {
      case 'read': return `Reading ${p ?? 'file'}...`
      case 'write_file': return `Writing ${p ?? 'file'}...`
      case 'edit_file': return `Editing ${p ?? 'file'}...`
      case 'glob': return `Finding files${pt ? `: ${pt}` : '...'}`
      case 'grep': return `Searching code${q ? ` for "${q.slice(0, 40)}"` : '...'}`
      case 'bash': return `Running${cmd ? `: ${cmd.slice(0, 50)}` : ' command...'}`
      case 'web_fetch': return `Fetching ${url ?? 'page'}...`
      default: return `${tool.replace(/_/g, ' ')}...`
    }
  }

  const toolIcon = (tool: string): string => {
    switch (tool) {
      case 'read': case 'write_file': case 'edit_file': return 'FileText'
      case 'glob': case 'grep': return 'Search'
      case 'bash': return 'Terminal'
      case 'web_fetch': return 'Globe'
      default: return 'Wrench'
    }
  }

  const toolSummary = (tool: string, args: Record<string, unknown>): string | undefined => {
    const p = args['path'] as string | undefined
    const q = args['query'] as string | undefined
    const pt = args['pattern'] as string | undefined
    const cmd = args['command'] as string | undefined
    const url = args['url'] as string | undefined
    switch (tool) {
      case 'read': return `Tool: read — Path: ${p ?? '?'}`
      case 'write_file': return `Tool: write_file — Path: ${p ?? '?'}`
      case 'edit_file': return `Tool: edit_file — Path: ${p ?? '?'}`
      case 'bash': return `Tool: bash — ${cmd ?? '?'}`
      case 'grep': return `Tool: grep — Pattern: ${q ?? pt ?? '?'}`
      case 'glob': return `Tool: glob — Pattern: ${pt ?? '?'}`
      case 'web_fetch': return `Tool: web_fetch — URL: ${url ?? '?'}`
      default: return `Tool: ${tool}`
    }
  }

  // ── Phase 8G: agent activity phases ─────────────────────────────────
  // The backend forwards small {"phase": ...} markers from the research and
  // coding workflows. Labels describe WHAT the agent is doing — never which
  // internal graph node executes.

  const PHASE_LABELS: Record<string, string> = {
    understanding: 'Understanding your question',
    decomposed: 'Exploring the question from multiple angles',
    searching: 'Searching for information',
    evaluating: 'Evaluating evidence quality',
    refining: 'Refining the search',
    deduplicated: 'Merging duplicate sources',
    synthesizing: 'Synthesizing findings',
    validating: 'Validating citations',
    coverage_incomplete: 'Search budget reached — some sub-questions remain unverified',
    verifying: 'Verifying the changes',
    verification_failed: 'Verification failed — analyzing what went wrong',
    verification_unavailable: 'Nothing could be verified — no commands available',
    retrying: 'Retrying with failure feedback',
  }

  const RETRY_REASON_LABELS: Record<string, string> = {
    insufficient_evidence: 'Evidence was insufficient',
    verification_failed: 'Verification failed',
    empty: 'Previous attempt produced nothing',
    max_steps: 'Previous attempt ran out of steps',
  }

  const phaseLabel = (ev: { phase?: string }): string =>
    PHASE_LABELS[ev.phase ?? ''] ?? `Working: ${ev.phase ?? ''}`

  const phaseDetail = (ev: {
    phase?: string; sub_questions?: number; gaps?: number;
    command?: string; exit_code?: number | null; new_sources?: number;
  }): string | undefined => {
    switch (ev.phase) {
      case 'decomposed':
        return ev.sub_questions ? `${ev.sub_questions} sub-questions` : undefined
      case 'refining':
        return ev.gaps ? `${ev.gaps} knowledge gap(s) to fill` : undefined
      case 'deduplicated':
        return 'no new sources — reusing what we have'
      case 'verification_failed':
        return [
          ev.command ? `command: ${ev.command}` : null,
          ev.exit_code != null ? `exit code ${ev.exit_code}` : null,
        ].filter(Boolean).join(' · ') || undefined
      default:
        return undefined
    }
  }

  const pushStep = useCallback((step: {
    type: 'thinking' | 'tool_call'
    icon: string
    label: string
    detail?: string
    query?: string
    toolCallId?: string
    toolName?: string
    toolCategory?: string
    toolSummary?: string
    status: 'running' | 'completed' | 'error'
  }) => {
    setInlineSteps(prev => {
      const now = Date.now()
      const closed = prev.map(s =>
        s.status === 'running' ? { ...s, status: 'completed' as const, durationMs: now - s.startedAt } : s
      )
      return [...closed, { ...step, id: nextId(), startedAt: now }]
    })
  }, [])

  const pushReasoning = useCallback((text: string) => {
    // Accumulate the live reasoning trace. It streams into the in-conversation
    // thinking panel while the model is thinking, then is attached to the
    // assistant message on the first token (see attachThought). Reasoning is
    // deliberately NOT an inline step — tool/agent steps own the sidebar.
    if (!thoughtStartedAtRef.current) thoughtStartedAtRef.current = Date.now()
    thoughtRef.current += text
    setThinking(true)
    setLiveThought(prev => prev + text)
  }, [])

  const handleEvent = useCallback(
    (ev: ServerEvent) => {
      switch (ev.type) {
        case 'token':
          appendToken(ev.text)
          break
        case 'thinking':
        case 'status': {
          // Honest search state: surface distinct icons per grounding_status
          const txt = (ev as any).text || ''
          let icon = 'Brain'
          let st: 'running' | 'error' = 'running'
          if (txt.includes('Search not configured')) {
            icon = 'Settings'
            st = 'error'
          } else if (txt.includes('Search failed')) {
            icon = 'AlertTriangle'
            st = 'error'
          } else if (txt.includes('No search results')) {
            icon = 'SearchX'
          }
          pushStep({
            type: 'thinking',
            icon,
            label: txt,
            detail: (ev as any).detail,
            query: (ev as any).query,
            status: st,
          })
          break
        }
        case 'reasoning':
          pushReasoning(ev.text)
          break
        case 'model':
          currentModelRef.current = ev.text
          break
        case 'agent_status':
          pushStep({
            type: 'thinking',
            icon: 'Activity',
            label: ev.text,
            detail: ev.detail,
            query: ev.query,
            status: 'running',
          })
          break
        case 'phase':
          pushStep({
            type: 'thinking',
            icon: 'Activity',
            label: phaseLabel(ev),
            detail: phaseDetail(ev),
            query: ev.query,
            status: ev.phase === 'verification_failed' ? 'error' : 'running',
          })
          break
        case 'retry':
          pushStep({
            type: 'thinking',
            icon: 'RefreshCw',
            label: `Retrying (attempt ${ev.attempt})`,
            detail: RETRY_REASON_LABELS[ev.reason] ?? ev.reason,
            query: ev.query,
            status: 'running',
          })
          break
        case 'plan':
          setPlan({ plan: ev.plan, status: 'pending' })
          break
        case 'tool_call':
          pushStep({
            type: 'tool_call',
            icon: toolIcon(ev.tool),
            label: toolLabel(ev.tool, ev.args),
            toolCallId: ev.id,
            toolName: ev.tool,
            toolCategory: ev.category,
            toolSummary: toolSummary(ev.tool, ev.args),
            status: 'running',
          })
          break
        case 'tool_result':
          setInlineSteps(prev => prev.map(s =>
            s.toolCallId === ev.id
              ? { ...s, status: 'completed' as const, durationMs: Date.now() - s.startedAt, result: ev.result, diff: ev.diff }
              : s
          ))
          break
        case 'directory_set':
          break
        case 'projects_list':
          setProjects(ev.projects)
          break
        case 'recent_conversations':
          break
        case 'project_created':
          setProjects(prev => [ev.project, ...prev])
          break
        case 'project_selected':
          break
        case 'background_run_update': {
          const isTerminal = ev.status === 'done' || ev.status === 'error' || ev.status === 'cancelled'
          // Computed from the current committed state (not the setState updater's
          // `prev`) so the transition check is a plain value, not a variable
          // mutated inside a closure — only notify on the transition into
          // done/error, never on repeated updates that are already terminal.
          const existing = backgroundRuns.find(r => r.run_id === ev.run_id)
          const wasTerminal = !!existing && ['done', 'error', 'cancelled'].includes(existing.status)
          const justFinished = isTerminal && !wasTerminal
            ? { status: ev.status, goal: existing?.goal || ev.goal || 'Background job' }
            : null

          setBackgroundRuns(prev => {
            const idx = prev.findIndex(r => r.run_id === ev.run_id)
            const run: BackgroundRunInfo = {
              run_id: ev.run_id,
              goal: ev.goal ?? prev[idx]?.goal ?? '',
              status: ev.status,
              created: prev[idx]?.created ?? new Date().toISOString(),
              ended: isTerminal ? new Date().toISOString() : '',
            }
            if (idx >= 0) {
              const next = [...prev]
              next[idx] = run
              return next
            }
            return [run, ...prev]
          })

          if (justFinished) {
            const goal = justFinished.goal
            const r = justFinished.status === 'error'
              ? notifyPolicy.jobFailed(goal)
              : notifyPolicy.jobCompleted(goal)
            pushNotification(r.draft)
            if (r.native) notifyIfUnfocused(r.native.title, r.native.body)
          }
          break
        }
        case 'background_run_list':
          setBackgroundRuns(ev.runs)
          setJobsLoading(false)
          setJobsError(null)
          break
        case 'schedule_list':
        case 'schedule_created':
        case 'schedule_deleted':
        case 'schedule_toggled':
          break
        case 'progress':
          setProgress({ current: ev.current, total: ev.total, label: ev.label })
          break
        case 'agent_state':
          setAgentState({
            current_goal: ev.current_goal,
            status: ev.status,
            tools_used: ev.tools_used,
            error: ev.error,
          })
          break
        case 'assistant_event':
          pushTimelineEntry(ev.entry)
          break
        case 'memory_activity':
          receiveMemory(ev.activity)
          break
        case 'permission_request': {
          setPermission({ tool: ev.tool, args: ev.args, id: ev.id, timeoutMs: (ev as any).timeoutMs, expiresAt: (ev as any).expiresAt })
          // Notification on pending — honest expiry visible even when user is elsewhere
          try {
            pushNotification({ severity: 'info', title: 'Permission required', message: `Novi wants to run ${ev.tool} — approve or deny` })
          } catch {}
          break
        }
        case 'permission_timeout': {
          // Honest expiry: the backend resolved the request as timed out and
          // did NOT perform the action. Distinct from deny (user decision)
          // and cancel (user stopped the run). Only clear the prompt when it
          // is the request that actually expired — never a newer prompt.
          const current = permissionRef.current
          if (current && current.id !== ev.id) break
          setPermission(null)
          const ownerId = owner?.conversationId
          if (ownerId) {
            const msg = 'Permission request expired — action not performed.'
            updateConversation(ownerId, (c) => ({
              ...c,
              updatedAt: 'Just now',
              messages: [...c.messages, { id: nextId(), role: 'assistant', content: msg, createdAt: now() }],
            }))
            dirtyIdRef.current = ownerId
          }
          break
        }
        case 'done': {
          const finishedId = owner?.conversationId
          const wasViewing = !!finishedId && finishedId === resolvedActiveId
          clearStopFallback()
          finishStreaming()
          setOwner(null)
          setPermission(null)
          setPlan(null)
          setProgress(null)
          setInlineSteps(prev => prev.map(s =>
            s.status === 'running' ? { ...s, status: 'completed' as const, durationMs: Date.now() - s.startedAt } : s
          ))
          if (finishedId) {
            const title = conversations.find(c => c.id === finishedId)?.title || 'a conversation'
            const r = notifyPolicy.responseReady(title, finishedId)
            // In-app history only matters for what you didn't already see happen live.
            if (!wasViewing) pushNotification(r.draft)
            // Native OS notification is keyed on window focus, not which conversation
            // was active — a minimized window still deserves a ping either way.
            if (r.native) notifyIfUnfocused(r.native.title, r.native.body)
          }
          break
        }
        case 'error': {
          const finishedId = owner?.conversationId
          const wasViewing = !!finishedId && finishedId === resolvedActiveId
          clearStopFallback()
          appendToken(`\n\n**Error:** ${ev.text}`)
          currentModelRef.current = ''
          setThinking(false)
          setLiveThought('')
          setOwner(null)
          setProgress(null)
          if (finishedId) {
            const title = conversations.find(c => c.id === finishedId)?.title || 'a conversation'
            const r = notifyPolicy.responseFailed(title, finishedId)
            if (!wasViewing) pushNotification(r.draft)
            if (r.native) notifyIfUnfocused(r.native.title, r.native.body)
          }
          break
        }
      }
    },
    [appendToken, pushStep, pushReasoning, finishStreaming, owner, resolvedActiveId, conversations, pushNotification, backgroundRuns, pushTimelineEntry, updateConversation]
  )

  const handleEventRef = useRef(handleEvent)
  handleEventRef.current = handleEvent

  useEffect(() => {
    const client = new NoviClient()
    client.onEvent = (ev) => handleEventRef.current(ev)
    client.onConnectionChange = setConnection
    client.connect()
    clientRef.current = client
    return () => {
      client.disconnect()
      if (clientRef.current === client) clientRef.current = null
    }
  }, [])

  // Reconnection awareness: surfacing a closed→open transition instead of
  // silently resuming. This does not touch the owner/streaming model — in-flight
  // state is intentionally left intact so an interrupted generation can resume.
  const [reconnected, setReconnected] = useState(false)
  const prevConnectionRef = useRef<ConnectionState>('connecting')
  useEffect(() => {
    const prev = prevConnectionRef.current
    prevConnectionRef.current = connection
    if (prev === 'closed' && connection === 'open') {
      const r = notifyPolicy.reconnected()
      pushNotification(r.draft)
      setReconnected(true)
      const t = window.setTimeout(() => setReconnected(false), 4000)
      return () => window.clearTimeout(t)
    }
  }, [connection, pushNotification])

  const sendMessage = useCallback(
    (content: string, attachments?: Attachment[], deepResearch?: boolean, projectIdOverride?: string | null) => {
      const client = clientRef.current
      // Single-flight: refuse a new generation while one is already owned.
      if (!client || owner) return
      const trimmed = content.trim()
      if (!trimmed && (!attachments || attachments.length === 0)) return
      const textToSend = trimmed || '(attachment)'

      // Find the project this conversation belongs to — convProject via conversationIds OR projectId field
      const conv = conversations.find(c => c.id === resolvedActiveId) as any
      const convProject = projects.find(p => p.conversationIds.includes(resolvedActiveId) || (conv?.projectId && conv.projectId === p.id))
      let effectiveProjectId: string | null = null
      if (projectIdOverride !== undefined) {
        effectiveProjectId = projectIdOverride
      } else {
        effectiveProjectId = convProject?.id ?? (resolvedActiveId === DRAFT_ID ? activeProjectId : null)
      }
      const projectId = effectiveProjectId ?? undefined

      if (resolvedActiveId === DRAFT_ID) {
        const newId = nextId()
        const newConv: Conversation = {
          id: newId,
          title: trimmed.slice(0, 48) || 'Attachments',
          updatedAt: 'Just now',
          pinned: false,
          projectId: effectiveProjectId,
          messages: [{ id: nextId(), role: 'user', content: textToSend, createdAt: now(), attachments }],
        } as Conversation
        if (!client.sendChat(textToSend, newId, attachments, projectId, deepResearch)) return
        setConversations((convs) => [newConv, ...convs])
        setActiveId(newId)
        if (effectiveProjectId) {
          setProjects(prev => prev.map(p => p.id === effectiveProjectId && !p.conversationIds.includes(newId) ? { ...p, conversationIds: [...p.conversationIds, newId] } : p))
        }
        setOwner({ conversationId: newId })
        setDeepResearchByConv((prev) => ({ ...prev, [newId]: !!deepResearch }))
        dirtyIdRef.current = newId
        thoughtRef.current = ''
        thoughtStartedAtRef.current = 0
        setThinking(false)
        setLiveThought('')
        setInlineSteps([])
      } else {
        // If caller explicitly scoped this send to a project that the current
        // conversation doesn't belong to, fork a NEW conversation in that
        // project rather than appending to an unrelated global chat.
        const curPid = (conversations.find(c => c.id === resolvedActiveId) as any)?.projectId as string | undefined
        const curProj = projects.find(p => p.id === projectId)
        const belongsToOverride = projectIdOverride !== undefined
          ? (projectId === curPid || !!curProj?.conversationIds.includes(resolvedActiveId))
          : true
        if (projectIdOverride !== undefined && projectId !== undefined && !belongsToOverride) {
          const newId = nextId()
          const newConv: Conversation = {
            id: newId,
            title: trimmed.slice(0, 48) || 'Attachments',
            updatedAt: 'Just now',
            pinned: false,
            projectId: projectIdOverride as string,
            messages: [{ id: nextId(), role: 'user', content: textToSend, createdAt: now(), attachments }],
          } as Conversation
          if (!client.sendChat(textToSend, newId, attachments, projectId, deepResearch)) return
          setConversations((convs) => [newConv, ...convs])
          setActiveId(newId)
          if (projectIdOverride) {
            setProjects(prev => prev.map(p => p.id === projectIdOverride && !p.conversationIds.includes(newId) ? { ...p, conversationIds: [...p.conversationIds, newId] } : p))
          }
          setOwner({ conversationId: newId })
          setDeepResearchByConv((prev) => ({ ...prev, [newId]: !!deepResearch }))
          dirtyIdRef.current = newId
          thoughtRef.current = ''
          thoughtStartedAtRef.current = 0
          setThinking(false)
          setLiveThought('')
          setInlineSteps([])
          return
        }
        if (!client.sendChat(textToSend, resolvedActiveId, attachments, projectId, deepResearch)) return
        const targetId = resolvedActiveId
        updateConversation(targetId, (c) => ({
          ...c,
          title: c.messages.length === 0 ? (trimmed.slice(0, 48) || 'Attachments') : c.title,
          updatedAt: 'Just now',
          messages: [
            ...c.messages,
            { id: nextId(), role: 'user', content: textToSend, createdAt: now(), attachments },
          ],
        }))
        setOwner({ conversationId: targetId })
        setDeepResearchByConv((prev) => ({ ...prev, [targetId]: !!deepResearch }))
        dirtyIdRef.current = targetId
        thoughtRef.current = ''
        thoughtStartedAtRef.current = 0
        setThinking(false)
        setLiveThought('')
        setInlineSteps([])
      }
    },
    [owner, updateConversation, resolvedActiveId, projects, conversations, activeProjectId]
  )

  const stop = useCallback(() => {
    clientRef.current?.stop()
    // If the backend never confirms (dropped connection, hung agent), don't
    // leave the UI stuck showing a generation forever.
    clearStopFallback()
    stopTimeoutRef.current = window.setTimeout(() => {
      finishStreaming()
      setOwner(null)
      setPermission(null)
      setPlan(null)
      setProgress(null)
      setInlineSteps([])
      stopTimeoutRef.current = null
    }, STOP_FALLBACK_MS)
  }, [finishStreaming])

  // Folder access is a session-scoped, read-only grant. The backend indexes
  // the selected path locally; no files are sent through the attachment API.
  const attachFolder = useCallback((path: string) => {
    return clientRef.current?.setDirectory(path) ?? false
  }, [])

  const answerPermission = useCallback((allowed: boolean, requestId?: string) => {
    clientRef.current?.answerPermission(allowed, requestId)
    setPermission(null)
  }, [])

  const answerPlan = useCallback((approved: boolean) => {
    clientRef.current?.answerPlan(approved)
    if (approved) {
      setPlan((p) => p ? { ...p, status: 'approved' } : null)
    } else {
      setPlan(null)
    }
  }, [])

  const handleStartBackgroundRun = useCallback((goal: string) => {
    if (!goal.trim()) return
    clientRef.current?.startBackgroundRun(goal.trim())
  }, [])

  const handleStopBackgroundRun = useCallback((runId: string) => {
    clientRef.current?.stopBackgroundRun(runId)
  }, [])

  const handleRefreshBackgroundRuns = useCallback(() => {
    setJobsLoading(true)
    setJobsError(null)
    const ok = clientRef.current?.listBackgroundRuns() ?? false
    if (!ok) {
      setJobsError('Could not refresh jobs — not connected')
      setJobsLoading(false)
      return
    }
    // WS reply will clear loading via background_run_list; fallback timeout
    window.setTimeout(() => setJobsLoading(false), 2000)
  }, [])

  const newChat = useCallback((projectId?: string | null) => {
    if (owner) return
    clientRef.current?.reset()
    setActiveId(DRAFT_ID)
    // Explicit project scope: set to that project, else clear for global unassigned chat
    if (projectId === undefined) {
      // legacy call without arg — clear global project scope so new chat is unassigned
      setActiveProjectId(null)
      try { localStorage.removeItem('novi_active_project_id') } catch {}
    } else if (projectId === null) {
      setActiveProjectId(null)
      try { localStorage.removeItem('novi_active_project_id') } catch {}
    } else {
      setActiveProjectId(projectId)
      try { localStorage.setItem('novi_active_project_id', projectId) } catch {}
    }
    setInlineSteps([])
    setThinking(false)
    setLiveThought('')
    setAgentState(null)
    setProgress(null)
  }, [owner])

  // Deep Research mode for the active conversation. Purely local UI state
  // threaded into the next message — the backend resolves the research
  // strategy/intent explicitly from the flag.
  const deepResearch = !!deepResearchByConv[resolvedActiveId]
  const toggleDeepResearch = useCallback(() => {
    setDeepResearchByConv((prev) => ({ ...prev, [resolvedActiveId]: !prev[resolvedActiveId] }))
  }, [resolvedActiveId])

  const pinConversation = useCallback((id: string) => {
    setConversations((convs) =>
      convs.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c))
    )
  }, [])

  const renameConversation = useCallback((id: string, title: string) => {
    setConversations((convs) =>
      convs.map((c) => (c.id === id ? { ...c, title } : c))
    )
  }, [])

  const deleteConversation = useCallback((id: string) => {
    if (!id) return
    deleteConversationApi(id).catch(() => {
      showError("Couldn't delete this conversation on the server — it may come back after a restart.")
    })
    setConversations((convs) => convs.filter((c) => c.id !== id))
    // Canonical: derived — prune local optimistically, no server project update
    setProjects(prev => prev.map(p => p.conversationIds.includes(id) ? { ...p, conversationIds: p.conversationIds.filter(cid => cid !== id) } : p))
    setActiveId((prev) => prev === id ? DRAFT_ID : prev)
  }, [showError])

  const addConversationToProject = useCallback((convId: string, projId: string) => {
    const conv = conversations.find(c => c.id === convId)
    if (!conv) return
    if ((conv as any).projectId === projId) return
    const updatedConv = { ...conv, projectId: projId } as Conversation
    setConversations(prev => prev.map(c => c.id === convId ? updatedConv : c))
    // optimistic project prune/add (derived will converge on next fetch)
    setProjects(prev => prev.map(p => {
      if (p.id === projId) {
        if (p.conversationIds.includes(convId)) return p
        return { ...p, conversationIds: [...p.conversationIds, convId] }
      }
      if (p.conversationIds.includes(convId)) {
        return { ...p, conversationIds: p.conversationIds.filter(cid => cid !== convId) }
      }
      return p
    }))
    // Canonical: move via conversation.projectId
    saveConversation(updatedConv).catch(() => showError("Couldn't add this conversation to the project."))
  }, [conversations, showError])

  const removeConversationFromProject = useCallback((convId: string, projId: string) => {
    const conv = conversations.find(c => c.id === convId)
    if (!conv) return
    const updatedConv = { ...conv, projectId: null } as Conversation
    setConversations(prev => prev.map(c => c.id === convId ? updatedConv : c))
    setProjects(prev => prev.map(p => p.id === projId ? { ...p, conversationIds: p.conversationIds.filter(cid => cid !== convId) } : p))
    saveConversation(updatedConv).catch(() => showError("Couldn't remove this conversation from the project."))
  }, [conversations, showError])

  const handleCreateProject = useCallback(async (name: string, description?: string, sharedContext?: string) => {
    const p = await createProject({ name, description, sharedContext })
    if (p) {
      setProjects(prev => [p, ...prev])
      // Do NOT auto-set activeProjectId — global New chat must stay unassigned.
      // Active project is set explicitly via + New chat in project or selecting project.
    } else {
      showError("Couldn't create the project.")
    }
    return p
  }, [showError])

  const handleUpdateProject = useCallback(async (id: string, data: Partial<Project>) => {
    const p = await updateProject(id, data)
    if (p) {
      setProjects(prev => prev.map(pr => pr.id === id ? p : pr))
    } else {
      showError("Couldn't save changes to the project.")
    }
    return p
  }, [showError])

  const handleDeleteProject = useCallback(async (id: string) => {
    try {
      await deleteProjectApi(id)
    } catch {
      showError("Couldn't delete the project on the server — it may come back after a restart.")
    }
    setProjects(prev => prev.filter(p => p.id !== id))
    if (activeProjectId === id) setActiveProjectId(null)
  }, [activeProjectId, showError])

  const active: Conversation = resolvedActiveId === DRAFT_ID
    ? { id: DRAFT_ID, title: 'New chat', updatedAt: '', pinned: false, messages: [] } as Conversation
    : conversations.find((c) => c.id === resolvedActiveId) ?? { id: DRAFT_ID, title: 'New chat', updatedAt: '', pinned: false, messages: [] } as Conversation

  // Project for active conversation — single source: conversation's projectId OR conversationIds membership, fallback to selected project for draft
  const convProjectForActive = (() => {
    if (resolvedActiveId === DRAFT_ID) return activeProjectId ? projects.find(p => p.id === activeProjectId) ?? null : null
    const convPid = (active as any).projectId as string | undefined
    if (convPid) {
      const byPid = projects.find(p => p.id === convPid)
      if (byPid) return byPid
    }
    return projects.find(p => p.conversationIds.includes(resolvedActiveId)) ?? null
  })()
  // Resolve project shared context for the active conversation — owning project wins, draft uses selected
  const activeProject = convProjectForActive ?? (activeProjectId ? projects.find(p => p.id === activeProjectId) ?? null : null)

  // Whether the conversation currently on screen is the one actually
  // generating. Everything below is gated on this, not on `owner` alone —
  // that's what stops a switch from redirecting the trace/plan/permission/
  // progress panels onto an unrelated conversation.
  const activeIsGenerating = owner !== null && owner.conversationId === resolvedActiveId

  const busyReason = owner !== null && owner.conversationId !== resolvedActiveId
    ? `Novi is responding in "${conversations.find(c => c.id === owner.conversationId)?.title ?? 'another conversation'}"`
    : null

  // Raw, unconditional — unlike everything above, these are NOT gated to the
  // active conversation. They're what a sidebar item, a global header pill,
  // or the landing page needs to answer "is Novi doing anything right now,
  // and where" regardless of what's currently on screen.
  const generatingConversationId = owner?.conversationId ?? null
  const generatingConversationTitle = generatingConversationId
    ? conversations.find(c => c.id === generatingConversationId)?.title ?? null
    : null

  return {
    connection,
    memoryActivity,
    conversationsHydrated,
    conversations,
    active,
    activeId: resolvedActiveId,
    setActiveId,
    generating: activeIsGenerating,
    busyReason,
    generatingConversationId,
    generatingConversationTitle,
    reconnected,
    inlineSteps: activeIsGenerating ? inlineSteps : [],
    thinking: activeIsGenerating ? thinking : false,
    liveThought: activeIsGenerating ? liveThought : '',
    agentState: activeIsGenerating ? agentState : null,
    progress: activeIsGenerating ? progress : null,
    plan: activeIsGenerating ? plan : null,
    permission: activeIsGenerating ? permission : null,
backgroundRuns,
    jobsError,
    jobsLoading,
    timeline,
    timelineError,
    timelineStatus,
    timelineLoading,
    refreshTimeline,
    projectsLoading,
    projectsError,
    refreshProjects,
    sendMessage,
    deepResearch,
    toggleDeepResearch,
    startBackgroundRun: handleStartBackgroundRun,
    stopBackgroundRun: handleStopBackgroundRun,
    refreshBackgroundRuns: handleRefreshBackgroundRuns,
    stop,
    attachFolder,
    answerPermission,
    answerPlan,
    newChat,
    pinConversation,
    renameConversation,
    deleteConversation,
    projects,
    activeProjectId,
    setActiveProjectId,
    activeProject,
    addConversationToProject,
    removeConversationFromProject,
    createProject: handleCreateProject,
    updateProject: handleUpdateProject,
    deleteProject: handleDeleteProject,
  }
}
