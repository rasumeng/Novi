import { useCallback, useEffect, useRef, useState } from 'react'
import { Conversation, InlineStep, Attachment, Project, PlanData, BackgroundRunInfo, AgentStateInfo, ProgressInfo, TimelineEntry } from '@/types'
import { CozmoClient, ConnectionState, ServerEvent, fetchConversations, saveConversation, deleteConversationApi, fetchProjects, createProject, updateProject, deleteProjectApi, fetchProjectConversations, fetchTimeline } from '@/services/cozmo'
import { useToast } from '@/hooks/useToast'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import { notifyPolicy } from '@/notifications/policy'
import { notifyIfUnfocused } from '@/native/tauri'
import { mergeTimeline } from '@/utils/timeline'

export interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
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

export function useCozmoChat() {
  const { showError } = useToast()
  const { push: pushNotification } = useNotificationCenter()
  const clientRef = useRef<CozmoClient | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState(() => '')

  // The single generation owner. null when nothing is in flight. This is the
  // only thing that decides where streaming events land — see module comment.
  const [owner, setOwner] = useState<GenerationOwner | null>(null)

  const [inlineSteps, setInlineSteps] = useState<InlineStep[]>([])
  const [permission, setPermission] = useState<PermissionRequest | null>(null)
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [backgroundRuns, setBackgroundRuns] = useState<BackgroundRunInfo[]>([])
  const currentModelRef = useRef('')
  const stopTimeoutRef = useRef<number | null>(null)

  const [agentState, setAgentState] = useState<AgentStateInfo | null>(null)
  const [progress, setProgress] = useState<ProgressInfo | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  // Milestone 4: assistant timeline feed. Live entries prepend from
  // `assistant_event`; history is hydrated via REST on mount.
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const pushTimelineEntry = useCallback((entry: TimelineEntry) => {
    setTimeline(prev => mergeTimeline([entry, ...prev]))
  }, [])
  const refreshTimeline = useCallback(() => {
    fetchTimeline().then((entries) => {
      if (entries.length) setTimeline(prev => mergeTimeline([...entries, ...prev]))
    }).catch(() => {})
  }, [])
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

  // Load conversations on mount
  useEffect(() => {
    fetchConversations()
      .then((list) => {
        setConversations(list)
      })
      .catch(() => {
        setConversations([])
        showError("Couldn't load your conversations. Is Cozmo's backend running?")
      })
    fetchProjects()
      .then((list) => setProjects(list))
      .catch(() => {
        showError("Couldn't load your projects.")
      })
  }, [showError])

  useEffect(() => clearStopFallback, [])

  // Milestone 4: hydrate the persisted assistant timeline on mount.
  useEffect(() => {
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
        showError("Couldn't save this conversation. Your changes may be lost if you close Cozmo.")
      })
    }
  })

  // activeId resolves lazily; fall back to draft
  const resolvedActiveId = activeId || conversations[0]?.id || DRAFT_ID

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
  const appendToken = useCallback(
    (text: string) => {
      const ownerId = owner?.conversationId
      if (!ownerId) return
      updateConversation(ownerId, (c) => {
        const msgs = [...c.messages]
        const last = msgs[msgs.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
          msgs[msgs.length - 1] = { ...last, content: last.content + text }
        } else {
          msgs.push({
            id: nextId(),
            role: 'assistant',
            content: text,
            createdAt: now(),
            streaming: true,
            model: currentModelRef.current || undefined,
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
    if (!ownerId) return
    updateConversation(ownerId, (c) => ({
      ...c,
      messages: c.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    }))
    dirtyIdRef.current = ownerId
  }, [owner, updateConversation])

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
    setInlineSteps(prev => {
      const last = prev[prev.length - 1]
      if (last && last.status === 'running' && last.icon === 'Brain' && last.label === 'Thinking...') {
        return prev.map((s, i) =>
          i === prev.length - 1
            ? { ...s, detail: (s.detail || '') + text }
            : s
        )
      }
      const now = Date.now()
      const closed: InlineStep[] = prev.map(s =>
        s.status === 'running' ? { ...s, status: 'completed' as const, durationMs: now - s.startedAt } : s
      )
      return [...closed, { id: nextId(), type: 'thinking' as const, icon: 'Brain', label: 'Thinking...', detail: text, status: 'running' as const, startedAt: now }]
    })
  }, [])

  const handleEvent = useCallback(
    (ev: ServerEvent) => {
      switch (ev.type) {
        case 'token':
          appendToken(ev.text)
          break
        case 'thinking':
        case 'status':
          pushStep({
            type: 'thinking',
            icon: 'Brain',
            label: ev.text,
            detail: ev.detail,
            query: ev.query,
            status: 'running',
          })
          break
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
        case 'permission_request':
          setPermission({ tool: ev.tool, args: ev.args })
          break
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
    [appendToken, pushStep, pushReasoning, finishStreaming, owner, resolvedActiveId, conversations, pushNotification, backgroundRuns, pushTimelineEntry]
  )

  const handleEventRef = useRef(handleEvent)
  handleEventRef.current = handleEvent

  useEffect(() => {
    const client = new CozmoClient()
    client.onEvent = (ev) => handleEventRef.current(ev)
    client.onConnectionChange = setConnection
    client.connect()
    clientRef.current = client
    return () => client.disconnect()
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
    (content: string, attachments?: Attachment[]) => {
      const client = clientRef.current
      // Single-flight: refuse a new generation while one is already owned.
      if (!client || owner) return
      const trimmed = content.trim()
      if (!trimmed && (!attachments || attachments.length === 0)) return
      const textToSend = trimmed || '(attachment)'

      // Find the project this conversation belongs to
      const convProject = projects.find(p => p.conversationIds.includes(resolvedActiveId))
      const projectId = convProject?.id

      if (resolvedActiveId === DRAFT_ID) {
        const newId = nextId()
        const newConv: Conversation = {
          id: newId,
          title: trimmed.slice(0, 48) || 'Attachments',
          updatedAt: 'Just now',
          pinned: false,
          messages: [{ id: nextId(), role: 'user', content: textToSend, createdAt: now(), attachments }],
        }
        if (!client.sendChat(textToSend, newId, attachments, projectId)) return
        setConversations((convs) => [newConv, ...convs])
        setActiveId(newId)
        setOwner({ conversationId: newId })
        dirtyIdRef.current = newId
        setInlineSteps([])
      } else {
        if (!client.sendChat(textToSend, resolvedActiveId, attachments, projectId)) return
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
        dirtyIdRef.current = targetId
        setInlineSteps([])
      }
    },
    [owner, updateConversation, resolvedActiveId, projects]
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

  const answerPermission = useCallback((allowed: boolean) => {
    clientRef.current?.answerPermission(allowed)
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
    clientRef.current?.listBackgroundRuns()
  }, [])

  const newChat = useCallback(() => {
    if (owner) return
    clientRef.current?.reset()
    setActiveId(DRAFT_ID)
    setInlineSteps([])
    setAgentState(null)
    setProgress(null)
  }, [owner])

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
    setActiveId((prev) => prev === id ? DRAFT_ID : prev)
  }, [showError])

  const addConversationToProject = useCallback((convId: string, projId: string) => {
    const proj = projects.find(p => p.id === projId)
    if (!proj || proj.conversationIds.includes(convId)) return
    const updated = { ...proj, conversationIds: [...proj.conversationIds, convId] }
    setProjects(prev => prev.map(p => p.id === projId ? updated : p))
    updateProject(projId, { conversationIds: updated.conversationIds }).catch(() => {
      showError("Couldn't add this conversation to the project.")
    })
  }, [projects, showError])

  const removeConversationFromProject = useCallback((convId: string, projId: string) => {
    const proj = projects.find(p => p.id === projId)
    if (!proj) return
    const updated = { ...proj, conversationIds: proj.conversationIds.filter(id => id !== convId) }
    setProjects(prev => prev.map(p => p.id === projId ? updated : p))
    updateProject(projId, { conversationIds: updated.conversationIds }).catch(() => {
      showError("Couldn't remove this conversation from the project.")
    })
  }, [projects, showError])

  const handleCreateProject = useCallback(async (name: string, description?: string, sharedContext?: string) => {
    const p = await createProject({ name, description, sharedContext })
    if (p) {
      setProjects(prev => [p, ...prev])
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

  // Resolve project shared context for the active conversation
  const activeProject = activeProjectId
    ? projects.find(p => p.id === activeProjectId) ?? null
    : null

  const active: Conversation = resolvedActiveId === DRAFT_ID
    ? { id: DRAFT_ID, title: 'New chat', updatedAt: '', pinned: false, messages: [] }
    : conversations.find((c) => c.id === resolvedActiveId) ?? conversations[0] ?? { id: DRAFT_ID, title: 'New chat', updatedAt: '', pinned: false, messages: [] }

  // Whether the conversation currently on screen is the one actually
  // generating. Everything below is gated on this, not on `owner` alone —
  // that's what stops a switch from redirecting the trace/plan/permission/
  // progress panels onto an unrelated conversation.
  const activeIsGenerating = owner !== null && owner.conversationId === resolvedActiveId

  const busyReason = owner !== null && owner.conversationId !== resolvedActiveId
    ? `Cozmo is responding in "${conversations.find(c => c.id === owner.conversationId)?.title ?? 'another conversation'}"`
    : null

  // Raw, unconditional — unlike everything above, these are NOT gated to the
  // active conversation. They're what a sidebar item, a global header pill,
  // or the landing page needs to answer "is Cozmo doing anything right now,
  // and where" regardless of what's currently on screen.
  const generatingConversationId = owner?.conversationId ?? null
  const generatingConversationTitle = generatingConversationId
    ? conversations.find(c => c.id === generatingConversationId)?.title ?? null
    : null

  return {
    connection,
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
    agentState: activeIsGenerating ? agentState : null,
    progress: activeIsGenerating ? progress : null,
    plan: activeIsGenerating ? plan : null,
    permission: activeIsGenerating ? permission : null,
backgroundRuns,
    timeline,
    refreshTimeline,
    sendMessage,
    startBackgroundRun: handleStartBackgroundRun,
    stopBackgroundRun: handleStopBackgroundRun,
    refreshBackgroundRuns: handleRefreshBackgroundRuns,
    stop,
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
