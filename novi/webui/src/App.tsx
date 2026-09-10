import { lazy, Suspense, useState, useCallback, useEffect, useRef, type ReactNode } from 'react'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Conversation } from '@/components/chat/Conversation'
import type { SectionId } from '@/components/settings/SettingsModal'
import { useNoviChat } from '@/hooks/useNoviChat'
import { TitleBar } from '@/components/common/TitleBar'
import type { NavItemId } from '@/components/sidebar/workspaceModes'

// These views are entered on demand.  Keeping them out of the initial chat
// bundle improves desktop startup without changing the user-facing routes.
const ProjectsPanel = lazy(() => import('@/components/projects/ProjectsPanel').then(
  ({ ProjectsPanel }) => ({ default: ProjectsPanel })
))
const TimelinePage = lazy(() => import('@/components/timeline/TimelinePage').then(
  ({ TimelinePage }) => ({ default: TimelinePage })
))
const SearchModal = lazy(() => import('@/components/search/SearchModal').then(
  ({ SearchModal }) => ({ default: SearchModal })
))
const SettingsModal = lazy(() => import('@/components/settings/SettingsModal').then(
  ({ SettingsModal }) => ({ default: SettingsModal })
))

function DeferredView({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="flex flex-1 items-center justify-center text-sm text-base-400">Loading…</div>}>
    {children}
  </Suspense>
}

function BackendLoadingScreen({ connected }: { connected: boolean }) {
  // This deliberately mirrors the native splash and the HTML boot layer in
  // index.html. Keeping all three the same prevents a visual jump while the
  // desktop shell hands off to React and React hydrates the workspace.
  const phase = connected ? 2 : 0
  const status = connected ? 'Loading conversations and memory…' : 'Starting local services…'
  return <main className="novi-boot">
    <section className="novi-boot__card" aria-live="polite">
      <div className="novi-boot__brand"><span className="novi-boot__mark">✦</span>NOVI DESKTOP</div>
      <div className="novi-boot__heading"><span className="novi-boot__spinner" /><h1>Starting Novi</h1></div>
      <p className="novi-boot__status">{status}</p>
      <div className="novi-boot__progress" />
      <ul className="novi-boot__steps">
        {['Start local services', 'Connect your workspace', 'Load conversations and memory'].map((label, index) => (
          <li key={label} className={index < phase ? 'done' : index === phase ? 'active' : ''}>
            <i className="novi-boot__dot" /><span>{label}</span>
          </li>
        ))}
      </ul>
      <p className="novi-boot__foot">Everything is running locally on your device.</p>
    </section>
  </main>
}

type WorkspaceLocation = {
  noviWorkspace: true
  section: NavItemId
  conversationId: string
  settingsOpen: boolean
  settingsSection: SectionId
  searchOpen: boolean
}

export default function App() {
  const [collapsed, setCollapsed] = useState(false)
  const [activeSection, setActiveSection] = useState<NavItemId>('conversations')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState<SectionId>('general')
  const [searchOpen, setSearchOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(() => {
    try {
      const cur = localStorage.getItem('novi_activity_panel')
      if (cur !== null) return cur === 'true'
      const legacy = localStorage.getItem('cozmo_activity_panel')
      if (legacy !== null) return legacy === 'true'
    } catch {}
    return false
  })
  const chat = useNoviChat()
  const historyReady = useRef(false)
  const workspaceRef = useRef<WorkspaceLocation | null>(null)

  const applyWorkspace = useCallback((location: WorkspaceLocation) => {
    setActiveSection(location.section)
    setSettingsOpen(location.settingsOpen)
    setSettingsSection(location.settingsSection)
    setSearchOpen(location.searchOpen)
    // An empty id intentionally represents the fresh-chat landing page.
    // Apply it as well; otherwise history restoration can leave the previous
    // conversation selected after the user presses + New chat.
    chat.setActiveId(location.conversationId)
  }, [chat])

  const pushWorkspaceHistory = useCallback((changes: Partial<WorkspaceLocation>) => {
    const current = workspaceRef.current
    if (!current) return
    const next = { ...current, ...changes, noviWorkspace: true } as WorkspaceLocation
    applyWorkspace(next)
    window.history.pushState(next, '')
  }, [])

  useEffect(() => {
    workspaceRef.current = {
      noviWorkspace: true,
      section: activeSection,
      conversationId: chat.activeId,
      settingsOpen,
      settingsSection,
      searchOpen,
    }
  }, [activeSection, chat.activeId, searchOpen, settingsOpen, settingsSection])

  useEffect(() => {
    if (chat.connection !== 'open' || !chat.conversationsHydrated || historyReady.current) return
    historyReady.current = true
    const initial = workspaceRef.current!
    // Add a guarded app entry after boot. Going back from it never reaches the
    // startup surface; it is immediately returned to this workspace state.
    window.history.pushState(initial, '')
    const restore = (event: PopStateEvent) => {
      const state = event.state
      if (state?.noviWorkspace) {
        applyWorkspace(state as WorkspaceLocation)
        return
      }
      // The entry before Novi's workspace is a browser/startup entry. Return to
      // the current Novi location instead of ever rendering the boot screen.
      window.history.go(1)
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [applyWorkspace, chat.connection, chat.conversationsHydrated])

  const handleSectionChange = useCallback((id: NavItemId) => {
    if (id === 'settings') {
      pushWorkspaceHistory({ settingsOpen: true, settingsSection: 'general', searchOpen: false })
      return
    }
    pushWorkspaceHistory({ section: id, settingsOpen: false, searchOpen: false })
  }, [pushWorkspaceHistory])

  const handleOpenSettings = useCallback((section?: SectionId) => {
    pushWorkspaceHistory({ settingsOpen: true, settingsSection: section ?? workspaceRef.current?.settingsSection ?? 'general', searchOpen: false })
  }, [pushWorkspaceHistory])

  const handleCreateSkill = useCallback(() => {
    pushWorkspaceHistory({ settingsOpen: true, settingsSection: 'skills', searchOpen: false })
  }, [pushWorkspaceHistory])

  const handleSelectConversation = useCallback((id: string) => {
    pushWorkspaceHistory({ section: 'conversations', conversationId: id, settingsOpen: false, searchOpen: false })
  }, [pushWorkspaceHistory])

  const handleToggleActivity = useCallback(() => {
    setActivityOpen(v => {
      const next = !v
      try { localStorage.setItem('novi_activity_panel', String(next)) } catch {}
      return next
    })
  }, [])

  const workingActivityTitle = chat.generatingConversationId
    ? chat.generatingConversationTitle
    : null

  const handleStartProjectConversation = useCallback((projectId: string) => {
    chat.newChat(projectId)
    setActiveSection('conversations')
  }, [chat])

  const handleSendInProject = useCallback((projectId: string, content: string) => {
    const conv = chat.conversations.find(c => c.id === chat.activeId) as any
    const proj = chat.projects.find(p => p.id === projectId)
    const activeInProject = !!proj && (!!conv && (conv.projectId === projectId || proj.conversationIds.includes(chat.activeId)))
    if (activeInProject) {
      chat.sendMessage(content)
    } else {
      chat.setActiveProjectId(projectId)
      ;(chat.sendMessage as any)(content, undefined, undefined, projectId)
    }
    setActiveSection('conversations')
  }, [chat])

  const handleSelectConversationInProject = useCallback((id: string) => {
    chat.setActiveId(id)
    // stay in projects section — user sees thread inside project
  }, [chat])

  const renderSection = () => {
    switch (activeSection) {
      case 'projects':
        return (
          <DeferredView><ProjectsPanel
            projects={chat.projects}
            conversations={chat.conversations}
            onCreateProject={chat.createProject}
            onUpdateProject={chat.updateProject}
            onDeleteProject={chat.deleteProject}
            onSelectConversation={handleSelectConversationInProject}
            onRemoveConversation={chat.removeConversationFromProject}
            onSelectProject={chat.setActiveProjectId}
            onStartProjectConversation={handleStartProjectConversation}
            activeProjectId={chat.activeProjectId}
            onSendInProject={handleSendInProject}
            activeConversationId={chat.activeId}
            connection={chat.connection}
            generating={chat.generating}
            onStop={chat.stop}
            onOpenFull={handleSelectConversation}
            loading={(chat as any).projectsLoading}
            error={(chat as any).projectsError}
            onRetry={(chat as any).refreshProjects}
          /></DeferredView>
        )
      case 'timeline':
        return (
          <DeferredView><TimelinePage
            entries={chat.timeline}
            onRefresh={chat.refreshTimeline}
            onOpenConversation={handleSelectConversation}
            error={(chat as any).timelineError}
            status={(chat as any).timelineStatus}
            loading={(chat as any).timelineLoading}
          /></DeferredView>
        )
      default:
        return (
          <Conversation
            conversation={chat.active}
            connection={chat.connection}
            generating={chat.generating}
            busyReason={chat.busyReason}
            inlineSteps={chat.inlineSteps}
            thinking={chat.thinking}
            liveThought={chat.liveThought}
            plan={chat.plan}
            permission={chat.permission}
            agentState={chat.agentState}
            progress={chat.progress}
            activeProject={chat.activeProject}
            backgroundRuns={chat.backgroundRuns}
            onSend={chat.sendMessage}
            onAttachFolder={chat.attachFolder}
            deepResearch={chat.deepResearch}
            onToggleDeepResearch={chat.toggleDeepResearch}
            onStop={chat.stop}
            onApprovePlan={() => chat.answerPlan(true)}
            onRejectPlan={() => chat.answerPlan(false)}
            onAnswerPermission={chat.answerPermission}
            onOpenSettings={handleOpenSettings}
            workingActivityTitle={workingActivityTitle}
            conversations={chat.conversations}
            onOpenConversation={handleSelectConversation}
            timeline={chat.timeline}
            activityOpen={activityOpen}
            onToggleActivity={handleToggleActivity}
          />
        )
    }
  }

  // The desktop shell can paint before the Python sidecar is ready. Keep a
  // single intentional startup surface until its session handshake completes.
  if (chat.connection !== 'open' || !chat.conversationsHydrated) {
    return <BackendLoadingScreen connected={chat.connection === 'open'} />
  }

  return (
    <div className="h-screen w-screen flex flex-col bg-base-950 text-base-100 overflow-hidden relative">
      <TitleBar
        connection={chat.connection}
        reconnected={chat.reconnected}
        workingActivityTitle={workingActivityTitle}
        isActiveConversation={activeSection === 'conversations' && chat.generating}
        onSelectConversation={handleSelectConversation}
        collapsed={collapsed}
        onToggleSidebar={() => setCollapsed(v => !v)}
        activityOpen={activityOpen}
        onToggleActivity={handleToggleActivity}
        onSearch={() => pushWorkspaceHistory({ searchOpen: true, settingsOpen: false })}
          onOpenSettings={() => handleOpenSettings()}
      />

      <div className="relative z-10 flex flex-1 min-h-0">
        <Sidebar
          collapsed={collapsed}
          conversations={chat.conversations}
          activeId={chat.activeId}
          onSelect={handleSelectConversation}
          onNewChat={() => {
            chat.newChat(null)
            pushWorkspaceHistory({
              section: 'conversations',
              conversationId: '',
              settingsOpen: false,
              searchOpen: false,
            })
          }}
          onNewChatInProject={handleStartProjectConversation}
          onPin={chat.pinConversation}
          onRename={chat.renameConversation}
          onDelete={chat.deleteConversation}
          activeSection={activeSection}
          onSectionChange={handleSectionChange}
          generatingConversationId={chat.generatingConversationId}
          projects={chat.projects}
          activeProjectId={chat.activeProjectId}
          onSelectProject={(id) => { chat.setActiveProjectId(id); setActiveSection('projects') }}
          onCreateProject={chat.createProject}
          onUpdateProject={chat.updateProject}
          onDeleteProject={chat.deleteProject}
        />

        {renderSection()}
      </div>

      <Suspense fallback={null}><SearchModal open={searchOpen} onClose={() => pushWorkspaceHistory({ searchOpen: false })} onSelect={handleSelectConversation} /></Suspense>
      <Suspense fallback={null}><SettingsModal
        open={settingsOpen}
        onClose={() => pushWorkspaceHistory({ settingsOpen: false })}
        initialSection={settingsSection}
        onCreateSkill={handleCreateSkill}
        onSectionChange={(section) => pushWorkspaceHistory({ settingsOpen: true, settingsSection: section })}
      /></Suspense>
    </div>
  )
}
