interface Props {
  /** True when the conversation currently on screen is the one generating. */
  isActiveConversation: boolean
  title: string | null
}

// Always visible in the header regardless of which conversation is open —
// the answer to "is Cozmo doing anything right now, and where," which the
// per-conversation trace panel deliberately can't tell you once you switch
// away from the one that's generating.
export function GlobalActivityIndicator({ isActiveConversation, title }: Props) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-accent/10 border border-accent/20 text-[11px] text-accent">
      <span className="flex gap-0.5">
        <span className="w-1 h-1 rounded-full bg-accent animate-glow" />
        <span className="w-1 h-1 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.2s' }} />
        <span className="w-1 h-1 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.4s' }} />
      </span>
      {isActiveConversation ? 'Responding' : title ? `Responding in "${title}"` : 'Responding elsewhere'}
    </div>
  )
}
