import { useState, useCallback } from 'react'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Conversation } from '@/components/chat/Conversation'
import { ProjectsPanel } from '@/components/projects/ProjectsPanel'
import { JobsPage } from '@/components/jobs/JobsPage'
import { TimelinePage } from '@/components/timeline/TimelinePage'
import { SearchModal } from '@/components/search/SearchModal'

import { SettingsModal, SectionId } from '@/components/settings/SettingsModal'
import { useNoviChat } from '@/hooks/useNoviChat'
import { TitleBar } from '@/components/common/TitleBar'
import type { NavItemId } from '@/components/sidebar/workspaceModes'

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

  const handleSectionChange = useCallback((id: NavItemId) => {
    if (id === 'settings') {
      setSettingsSection('general')
      setSettingsOpen(true)
      return
    }
    setActiveSection(id)
  }, [])

  const handleOpenSettings = useCallback((section?: SectionId) => {
    if (section) setSettingsSection(section)
    setSettingsOpen(true)
  }, [])

  const handleCreateSkill = useCallback(() => {
    setSettingsSection('skills')
    setSettingsOpen(true)
  }, [])

  const handleSelectConversation = useCallback((id: string) => {
    chat.setActiveId(id)
    setActiveSection('conversations')
  }, [chat])

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

  const renderSection = () => {
    switch (activeSection) {
      case 'projects':
        return (
          <ProjectsPanel
            projects={chat.projects}
            conversations={chat.conversations}
            onCreateProject={chat.createProject}
            onUpdateProject={chat.updateProject}
            onDeleteProject={chat.deleteProject}
            onSelectConversation={(id) => { chat.setActiveId(id); setActiveSection('conversations') }}
            onRemoveConversation={chat.removeConversationFromProject}
            onSelectProject={chat.setActiveProjectId}
            onStartProjectConversation={handleStartProjectConversation}
            activeProjectId={chat.activeProjectId}
          />
        )
      case 'jobs':
        return (
          <JobsPage
            runs={chat.backgroundRuns}
            onStart={chat.startBackgroundRun}
            onStop={chat.stopBackgroundRun}
            onRefresh={chat.refreshBackgroundRuns}
          />
        )
      case 'timeline':
        return (
          <TimelinePage
            entries={chat.timeline}
            onRefresh={chat.refreshTimeline}
            onOpenConversation={handleSelectConversation}
          />
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
        onSearch={() => setSearchOpen(true)}
        onOpenSettings={() => handleOpenSettings()}
      />

      <div className="relative z-10 flex flex-1 min-h-0">
        <Sidebar
          collapsed={collapsed}
          conversations={chat.conversations}
          activeId={chat.activeId}
          onSelect={handleSelectConversation}
          onNewChat={() => { chat.newChat(null); setActiveSection('conversations') }}
          onNewChatInProject={handleStartProjectConversation}
          onPin={chat.pinConversation}
          onRename={chat.renameConversation}
          onDelete={chat.deleteConversation}
          activeSection={activeSection}
          onSectionChange={handleSectionChange}
          jobsCount={chat.backgroundRuns.length}
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

      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} onSelect={handleSelectConversation} />
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        initialSection={settingsSection}
        onCreateSkill={handleCreateSkill}
      />
    </div>
  )
}
