import { fetchConversations, fetchProjects, fetchTimelineEnvelope } from '@/services/novi'
import type { Conversation, Project } from '@/types'
import type { TimelineEnvelope } from '@/services/novi'

let conversationsCache: Conversation[] | null = null
let conversationsInflight: Promise<Conversation[]> | null = null

let projectsCache: Project[] | null = null
let projectsInflight: Promise<Project[]> | null = null

let timelineCache: TimelineEnvelope | null = null
let timelineInflight: Promise<TimelineEnvelope> | null = null

export function getConversationsCache(): Conversation[] | null {
  return conversationsCache
}

export function getProjectsCache(): Project[] | null {
  return projectsCache
}

export function getTimelineEnvelopeCache(): TimelineEnvelope | null {
  return timelineCache
}

export async function fetchConversationsDeduped(opts?: { force?: boolean }): Promise<Conversation[]> {
  if (!opts?.force && conversationsCache !== null) return conversationsCache
  if (conversationsInflight) return conversationsInflight
  const p = fetchConversations()
    .then((v) => {
      conversationsCache = v
      return v
    })
  conversationsInflight = p
  try {
    return await p
  } catch (e) {
    throw e
  } finally {
    conversationsInflight = null
  }
}

export async function fetchProjectsDeduped(opts?: { force?: boolean }): Promise<Project[]> {
  if (!opts?.force && projectsCache !== null) return projectsCache
  if (projectsInflight) return projectsInflight
  const p = fetchProjects()
    .then((v) => {
      projectsCache = v
      return v
    })
  projectsInflight = p
  try {
    return await p
  } catch (e) {
    throw e
  } finally {
    projectsInflight = null
  }
}

export async function fetchTimelineEnvelopeDeduped(opts?: { force?: boolean }): Promise<TimelineEnvelope> {
  if (!opts?.force && timelineCache !== null) return timelineCache
  if (timelineInflight) return timelineInflight
  const p = fetchTimelineEnvelope()
    .then((v) => {
      timelineCache = v
      return v
    })
  timelineInflight = p
  try {
    return await p
  } catch (e) {
    throw e
  } finally {
    timelineInflight = null
  }
}

export function resetBootCache(): void {
  conversationsCache = null
  conversationsInflight = null
  projectsCache = null
  projectsInflight = null
  timelineCache = null
  timelineInflight = null
}
