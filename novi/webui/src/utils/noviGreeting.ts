// utils/noviGreeting.ts
// Deterministic, time-aware greeting — no LLM, no personality engine.
// Picks a stable line per session (seeded by today's date) from small fixed
// arrays keyed to time of day, so it varies day-to-day but doesn't flicker
// on every re-render within a session.

const MORNING: string[] = [
  'Morning. What are we starting with?',
  'Hey, good morning.',
  'Coffee first, or jump right in?',
]

const AFTERNOON: string[] = [
  "Hey, what's on your mind?",
  'Good afternoon. What are we working on?',
  "Back again — what's up?",
]

const EVENING: string[] = [
  "Evening. What's on the agenda?",
  "Hey, how's it going tonight?",
  'Good evening. What can I help with?',
]

const LATE_NIGHT: string[] = [
  'Up late? I\'m here either way.',
  "Hey, night owl. What's on your mind?",
  'Still going — what do you need?',
]

function pickPool(hour: number): string[] {
  if (hour >= 5 && hour < 12) return MORNING
  if (hour >= 12 && hour < 17) return AFTERNOON
  if (hour >= 17 && hour < 22) return EVENING
  return LATE_NIGHT
}

// Simple seed from the date so the greeting is stable across re-renders in
// the same session but changes day to day.
function seedFromDate(date: Date): number {
  const key = date.getFullYear() * 10000 + (date.getMonth() + 1) * 100 + date.getDate()
  let hash = 0
  for (const ch of String(key)) {
    hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  }
  return hash
}

export function getNoviGreeting(): string {
  const now = new Date()
  const pool = pickPool(now.getHours())
  const seed = seedFromDate(now)
  return pool[seed % pool.length]
}