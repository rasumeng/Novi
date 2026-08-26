import type { SettingSchema } from './api'

interface Props {
  setting: SettingSchema
  value: unknown
  onChange: (id: string, value: unknown) => void
}

/** Render a single registered setting by type — the schema-driven build block. */
export function SettingField({ setting, value, onChange }: Props) {
  return (
    <div className="flex items-center justify-between gap-4 p-3 rounded-xl bg-base-800/50 border border-base-700">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-base-100">{setting.label}</p>
        {setting.description && (
          <p className="text-xs text-base-500 mt-0.5">{setting.description}</p>
        )}
        {setting.restart_required && (
          <span className="inline-flex items-center mt-1 px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-400">
            Restart required
          </span>
        )}
      </div>
      <div className="shrink-0">
        <FieldControl setting={setting} value={value} onChange={onChange} />
      </div>
    </div>
  )
}

function FieldControl({ setting, value, onChange }: Props) {
  switch (setting.type) {
    case 'bool':
      return (
        <button
          type="button"
          role="switch"
          aria-checked={!!value}
          onClick={() => onChange(setting.id, !value)}
          className={`relative inline-flex h-5 w-10 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ${
            value ? 'bg-accent' : 'bg-base-700'
          }`}
        >
          <span
            className={`pointer-events-none inline-block h-4 w-4 translate-x-0 rounded-full bg-white shadow ring-0 transition-transform duration-200 ${
              value ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      )
    case 'enum':
      return (
        <select
          value={String(value ?? '')}
          onChange={(e) => onChange(setting.id, e.target.value)}
          className="bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40 min-w-[140px]"
        >
          {setting.options.map((o) => (
            <option key={String(o.value)} value={String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
      )
    case 'model':
      return (
        <select
          value={String(value ?? '')}
          onChange={(e) => onChange(setting.id, e.target.value)}
          className="bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono outline-none focus:border-accent/40 min-w-[180px]"
        >
          <option value="">Not configured</option>
          {setting.options.map((o) => (
            <option key={String(o.value)} value={String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
      )
    case 'secret':
      return <SecretField setting={setting} value={value} onChange={onChange} />
    case 'int':
    case 'float':
      return (
        <input
          type="number"
          step={setting.type === 'float' ? '0.1' : '1'}
          value={value == null ? '' : String(value)}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') { onChange(setting.id, null); return }
            onChange(setting.id, setting.type === 'float' ? parseFloat(raw) : parseInt(raw, 10))
          }}
          className="w-20 bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono text-right outline-none focus:border-accent/40"
        />
      )
    default:
      // string / json
      return (
        <input
          type="text"
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(setting.id, e.target.value)}
          className="w-48 bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono outline-none focus:border-accent/40"
        />
      )
  }
}

function SecretField({ setting, value, onChange }: Props) {
  const plain = value == null ? '' : String(value)
  return (
    <input
      type="text"
      value={plain}
      placeholder={setting.default ? String(setting.default) : ''}
      onChange={(e) => onChange(setting.id, e.target.value)}
      className="w-48 bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-xs text-base-200 font-mono outline-none focus:border-accent/40"
    />
  )
}