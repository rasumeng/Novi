import clsx from 'clsx'

// Cosmetic tones only — the badge never implies a model role or capability.
// Color is picked deterministically from the model name, never from a
// hardcoded role vocabulary.
const BADGE_TONES = [
  'bg-accent/10 text-accent border-accent/20',
  'bg-sky-500/10 text-sky-400 border-sky-500/20',
  'bg-violet-500/10 text-violet-400 border-violet-500/20',
  'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  'bg-amber-500/10 text-amber-400 border-amber-500/20',
  'bg-rose-500/10 text-rose-400 border-rose-500/20',
]

function toneFor(model: string): string {
  let h = 0
  for (let i = 0; i < model.length; i++) {
    h = (h * 31 + model.charCodeAt(i)) >>> 0
  }
  return BADGE_TONES[h % BADGE_TONES.length]
}

export function ModelBadge({ model }: { model: string }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-md border', toneFor(model))}>
      {model}
    </span>
  )
}