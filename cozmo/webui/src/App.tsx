import { useState, useCallback } from 'react'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Conversation } from '@/components/chat/Conversation'
import { ProjectsPanel } from '@/components/projects/ProjectsPanel'
import { JobsPage } from '@/components/jobs/JobsPage'
import { TimelinePage } from '@/components/timeline/TimelinePage'

import { SettingsModal, SectionId } from '@/components/settings/SettingsModal'
import { useCozmoChat } from '@/hooks/useCozmoChat'
import { TitleBar } from '@/components/common/TitleBar'
import type { NavItemId } from '@/components/sidebar/workspaceModes'
import { NAV_ITEMS } from '@/components/sidebar/workspaceModes'

export default function App() {
  const [collapsed, setCollapsed] = useState(false)
  const [activeSection, setActiveSection] = useState<NavItemId>('conversations')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState<SectionId>('general')
  const chat = useCozmoChat()

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

  // Item 2: navigate to a conversation from the notification center. Reuses
  // the existing conversation state directly — no duplicated navigation logic.
  const handleSelectConversation = useCallback((id: string) => {
    chat.setActiveId(id)
    setActiveSection('conversations')
  }, [chat])

  // Item 1: source for the global activity pill — whichever conversation owns
  // the in-flight generation, not the one on screen.
  const workingActivityTitle = chat.generatingConversationId
    ? chat.generatingConversationTitle
    : null

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
            onOpenConversation={chat.setActiveId}
            timeline={chat.timeline}
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
        contextTitle={
          activeSection === 'conversations'
            ? chat.active.title
            : NAV_ITEMS[activeSection].label
        }
      />

      <div className="relative z-10 flex flex-1 min-h-0">
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((v) => !v)}
          conversations={chat.conversations}
          activeId={chat.activeId}
          onSelect={chat.setActiveId}
          onNewChat={() => { chat.newChat(); setActiveSection('conversations') }}
          onPin={chat.pinConversation}
          onRename={chat.renameConversation}
          onDelete={chat.deleteConversation}
          activeSection={activeSection}
          onSectionChange={handleSectionChange}
          jobsCount={chat.backgroundRuns.length}
          generatingConversationId={chat.generatingConversationId}
        />

        {renderSection()}
      </div>

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        initialSection={settingsSection}
        onCreateSkill={handleCreateSkill}
      />
    </div>
  )
}
