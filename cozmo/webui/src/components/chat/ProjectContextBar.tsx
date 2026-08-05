import { FolderKanban, GitBranch } from 'lucide-react'
import { Project } from '@/types'

interface Props {
  project: Project | null
  branch?: string
  modifiedFiles?: number
}

export function ProjectContextBar({ project, branch, modifiedFiles }: Props) {
  if (!project) return null

  return (
    <div className="flex items-center gap-3 px-5 py-1.5 bg-base-900/50 border-b border-base-800/50 text-[11px]">
      <div className="flex items-center gap-1.5 text-accent">
        <FolderKanban size={11} />
        <span className="font-medium">{project.name}</span>
      </div>
      {branch && (
        <div className="flex items-center gap-1 text-base-500">
          <GitBranch size={10} />
          {branch}
        </div>
      )}
      {modifiedFiles !== undefined && modifiedFiles > 0 && (
        <span className="text-base-500">
          {modifiedFiles} modified file{modifiedFiles !== 1 ? 's' : ''}
        </span>
      )}
    </div>
  )
}