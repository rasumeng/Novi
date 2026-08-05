import { useState, useCallback } from 'react'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Conversation } from '@/components/chat/Conversation'
import { ProjectsPanel } from '@/components/projects/ProjectsPanel'
import { JobsPage } from '@/components/jobs/JobsPage'

import { SettingsModal, SectionId } from '@/components/settings/SettingsModal'
import { useCozmoChat } from '@/hooks/useCozmoChat'
import type { NavItemId } from '@/components/sidebar/workspaceModes'

export default function App() {
  const [collapsed, setCollapsed] = useState(false)
  const [activeSection, setActiveSection] = useState<NavItemId>('conversations')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState<SectionId>('models')
  const chat = useCozmoChat()

  const handleSectionChange = useCallback((id: NavItemId) => {
    if (id === 'settings') {
      setSettingsSection('models')
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
      default:
        return (
          <Conversation
            conversation={chat.active}
            connection={chat.connection}
            generating={chat.generating}
            busyReason={chat.busyReason}
            inlineSteps={chat.inlineSteps}
            plan={chat.plan}
            permission={chat.permission}
            agentState={chat.agentState}
            progress={chat.progress}
            activeProject={chat.activeProject}
            backgroundRuns={chat.backgroundRuns}
            onSend={chat.sendMessage}
            onStop={chat.stop}
            onApprovePlan={() => chat.answerPlan(true)}
            onRejectPlan={() => chat.answerPlan(false)}
            onAnswerPermission={chat.answerPermission}
            onOpenSettings={handleOpenSettings}
          />
        )
    }
  }

  return (
    <div className="h-screen w-screen flex bg-base-950 text-base-100 overflow-hidden relative">
      <div className="absolute inset-0 pointer-events-none z-0" />
      <div className="relative z-10 flex flex-1">
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
