import { describe, it, expect } from 'vitest'
import { mergeTimeline, groupByDay } from './timeline'
import type { TimelineEntry } from '@/types'

const entry = (id: string, timestamp: string, kind = 'conversation.observed'): TimelineEntry => ({
  id,
  kind,
  title: `title-${id}`,
  detail: `detail-${id}`,
  timestamp,
})

describe('mergeTimeline', () => {
  it('dedupes by per-row id and sorts newest-first', () => {
    const newer = entry('a', '2026-01-03T10:00:00Z')
    const older = entry('c', '2026-01-01T10:00:00Z')
    const same = entry('a', '2026-01-03T10:00:00Z')
    const merged = mergeTimeline([older, newer, same, null])

    expect(merged.map((e) => e.id)).toEqual(['a', 'c'])
  })

  it('drops null/undefined and id-less rows', () => {
    const merged = mergeTimeline([entry('a', '2026-01-01T10:00:00Z'), null, undefined])
    expect(merged).toHaveLength(1)
  })
})

describe('groupByDay', () => {
  const now = new Date('2026-08-05T12:00:00Z')

  it('labels Today and Yesterday correctly', () => {
    const groups = groupByDay(
      [
        entry('b', '2026-08-05T09:00:00Z'),
        entry('a', '2026-08-04T09:00:00Z'),
      ],
      now
    )
    expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday'])
    expect(groups[0].entries.map((e) => e.id)).toEqual(['b'])
  })

  it('falls back to an Earlier label for older days', () => {
    const groups = groupByDay([entry('a', '2026-07-01T09:00:00Z')], now)
    expect(groups[0].label).not.toBe('Today')
    expect(groups[0].label).not.toBe('Yesterday')
  })
})