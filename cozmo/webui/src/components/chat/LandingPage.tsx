import { Sparkles, Globe, FileSymlink, Mail, BarChart3, Search, GitBranch, Braces, Bug, Code2, Lightbulb, PenLine } from 'lucide-react'
import { ConnectionState } from '@/services/cozmo'
import { CONNECTION_LABEL } from './connectionStatus'

interface Props {
  onSuggestion?: (text: string) => void
  connection: ConnectionState
}

interface SuggestionItem {
  icon: React.ElementType
  label: string
  prompt: string
}

const SUGGESTIONS: SuggestionItem[] = [
  { icon: Globe, label: 'Research a topic', prompt: 'Research the topic of ' },
  { icon: FileSymlink, label: 'Summarize this', prompt: 'Summarize this: ' },
  { icon: Mail, label: 'Draft an email', prompt: 'Draft an email about ' },
  { icon: BarChart3, label: 'Analyze data', prompt: 'Analyze this data: ' },
  { icon: Code2, label: 'Explain code', prompt: 'Explain this code: ' },
  { icon: Search, label: 'Find a bug', prompt: 'Find bugs in this code: ' },
  { icon: GitBranch, label: 'Review PR', prompt: 'Review this pull request: ' },
  { icon: Braces, label: 'Refactor code', prompt: 'Refactor this code: ' },
  { icon: Bug, label: 'Debug error', prompt: 'Help me debug this error: ' },
  { icon: Lightbulb, label: 'Brainstorm ideas', prompt: 'Help me brainstorm ' },
  { icon: PenLine, label: 'Write documentation', prompt: 'Write documentation for ' },
]

export function LandingPage({ onSuggestion, connection }: Props) {
  const status = CONNECTION_LABEL[connection]
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-16">
      <div className="w-14 h-14 rounded-2xl bg-accent/15 flex items-center justify-center mb-5">
        <Sparkles size={28} className="text-accent" />
      </div>
      <h1 className="text-xl font-semibold text-base-100 mb-1">What would you like to do?</h1>
      <p className="text-sm text-base-500 mb-8">Research, code, analyze, write, debug, and more.</p>

      <div className="grid grid-cols-2 gap-3 max-w-md">
        {SUGGESTIONS.map((s) => {
          const SI = s.icon
          return (
            <button
              key={s.label}
              onClick={() => onSuggestion?.(s.prompt)}
              className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-base-800/40 border border-base-700/60 hover:border-accent/30 text-base-400 hover:text-base-200 text-xs font-medium transition-all text-left"
            >
              <div className="w-7 h-7 rounded-lg bg-accent/10 text-accent border border-accent/20 flex items-center justify-center shrink-0">
                <SI size={14} />
              </div>
              {s.label}
            </button>
          )
        })}
      </div>

      <div className="mt-8 flex items-center gap-2 text-[11px] text-base-600">
        <span className={`w-1.5 h-1.5 rounded-full ${status.dot}`} />
        {status.text}
      </div>
    </div>
  )
}