import { lazy, Suspense, useState, useCallback, useEffect, useRef } from 'react'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Conversation } from '@/components/chat/Conversation'
import type { SectionId } from '@/components/settings/SettingsModal'
import { useNoviChat } from '@/hooks/useNoviChat'
import { useBoot } from '@/hooks/useBoot'
import { TitleBar } from '@/components/common/TitleBar'
import type { NavItemId } from '@/components/sidebar/workspaceModes'

// Lazy views — no loading fallback; fresh loading design will decide UX.
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

type WorkspaceLocation = {
  noviWorkspace: true
  section: NavItemId
  conversationId: string
  settingsOpen: boolean
  settingsSection: SectionId
  searchOpen: boolean
}

export default function App() {
  const boot = useBoot()
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
    const initial = workspaceRef.current!
    window.history.pushState(initial, '')
    const restore = (event: PopStateEvent) => {
      const state = event.state
      if (state?.noviWorkspace) {
        applyWorkspace(state as WorkspaceLocation)
        return
      }
      window.history.go(1)
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [applyWorkspace])

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
          <Suspense fallback={null}><ProjectsPanel
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
          /></Suspense>
        )
      case 'timeline':
        return (
          <Suspense fallback={null}><TimelinePage
            entries={chat.timeline}
            onRefresh={chat.refreshTimeline}
            onOpenConversation={handleSelectConversation}
            error={(chat as any).timelineError}
            status={(chat as any).timelineStatus}
            loading={(chat as any).timelineLoading}
          /></Suspense>
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
          boot={boot}
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
