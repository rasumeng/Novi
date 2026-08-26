import { PERM_MODES } from './constants'

interface Props {
  value: string
  onChange: (v: string) => void
}

export function PermissionSelect({ value, onChange }: Props) {
  return (
    <div className="flex rounded-lg overflow-hidden border border-base-700 bg-base-900">
      {PERM_MODES.map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={`px-2.5 py-1 text-[11px] font-medium transition-colors ${
            value === m
              ? m === 'allow' ? 'bg-emerald-500/20 text-emerald-400'
                : m === 'deny' ? 'bg-red-500/20 text-red-400'
                : 'bg-amber-500/20 text-amber-400'
              : 'text-base-500 hover:text-base-300 hover:bg-base-800'
          }`}
        >
          {m.charAt(0).toUpperCase() + m.slice(1)}
        </button>
      ))}
    </div>
  )
}
