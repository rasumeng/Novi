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
    <div className="flex justify-center pt-3 pb-1">
      <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-base-800/70 border border-base-700/30 backdrop-blur text-[11px] shadow-sm">
        <FolderKanban size={11} className="text-accent" />
        <span className="font-medium text-base-200">{project.name}</span>
        {branch && (
          <>
            <span className="text-base-600">·</span>
            <span className="flex items-center gap-1 text-base-500">
              <GitBranch size={10} />
              {branch}
            </span>
          </>
        )}
        {modifiedFiles !== undefined && modifiedFiles > 0 && (
          <>
            <span className="text-base-600">·</span>
            <span className="text-base-500">{modifiedFiles} modified</span>
          </>
        )}
      </div>
    </div>
  )
}
