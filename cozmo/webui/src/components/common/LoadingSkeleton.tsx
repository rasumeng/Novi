// Shimmer loading placeholder. Uses the existing `animate-shimmer` tailwind
// animation + base palette tokens so it matches the rest of the shell without
// introducing new colors. Drop into any surface that hydrates asynchronously
// to avoid blank screens.

interface Props {
  rows?: number
  /** Render as a single constrained block (e.g. inside a modal body). */
  compact?: boolean
  className?: string
}

export function LoadingSkeleton({ rows = 3, compact = false, className = '' }: Props) {
  return (
    <div className={`flex flex-col gap-3 ${compact ? '' : 'p-4'} ${className}`} aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="w-full h-9 rounded-xl bg-base-800 animate-shimmer"
          style={{ opacity: 1 - i * 0.18 }}
        />
      ))}
    </div>
  )
}