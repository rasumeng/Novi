import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Zap, Sparkles, Gem, SlidersHorizontal, ChevronDown, CircleHelp, CheckCircle2, X } from 'lucide-react'
import type { PerformanceProfile, PerformanceProfileId } from '@/product/types'
import type { PresetModelSummary } from '@/product/configLayer'

const PROFILE_ICON: Record<PerformanceProfileId, React.ElementType> = {
  lightweight: Zap,
  balanced: Sparkles,
  high_quality: Gem,
  custom: SlidersHorizontal,
}

interface Props {
  profiles: PerformanceProfile[]
  activeProfileId: PerformanceProfileId
  profileSummaries: Record<PerformanceProfileId, PresetModelSummary[]>
  onSelect: (id: PerformanceProfileId) => void
}

// The primary entry point to Performance Profiles — meant to feel like
// choosing how Cozmo behaves, not flipping a setting. Each card answers what
// it's for and who it's for up front; "Models included" and "Learn more" are
// progressive disclosure, not required reading.
export function ProfilePicker({ profiles, activeProfileId, profileSummaries, onSelect }: Props) {
  const [expandedId, setExpandedId] = useState<PerformanceProfileId | null>(null)
  const [learnMoreProfile, setLearnMoreProfile] = useState<PerformanceProfile | null>(null)

  return (
    <div className="space-y-2.5">
      {profiles.map((profile) => {
        const Icon = PROFILE_ICON[profile.id]
        const isActive = profile.id === activeProfileId
        const summary = profileSummaries[profile.id] ?? []
        const expanded = expandedId === profile.id

        return (
          <div
            key={profile.id}
            className={`rounded-2xl border overflow-hidden transition-colors ${
              isActive ? 'border-accent/50 bg-accent/[0.06]' : 'border-base-700 bg-base-800/40 hover:border-base-600'
            }`}
          >
            <button
              onClick={() => onSelect(profile.id)}
              aria-pressed={isActive}
              className="w-full text-left p-4 flex items-start gap-3"
            >
              <div
                className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${
                  isActive ? 'bg-accent/20 text-accent' : 'bg-base-800 text-base-400'
                }`}
              >
                <Icon size={17} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-base-100">{profile.label}</p>
                  {isActive && <CheckCircle2 size={14} className="text-accent shrink-0" />}
                </div>
                <p className="text-xs text-base-400 mt-0.5">{profile.intendedUse}</p>
                <p className="text-xs text-base-500 mt-1 leading-relaxed">{profile.description}</p>
                <span className="inline-flex items-center mt-2 px-2 py-0.5 rounded-full bg-base-800 border border-base-700 text-[10px] text-base-400">
                  {profile.recommendedHardware}
                </span>
              </div>
            </button>

            <div className="flex items-center gap-1 px-4 pb-3 pt-1 border-t border-base-800/60">
              {summary.length > 0 && (
                <button
                  onClick={() => setExpandedId(expanded ? null : profile.id)}
                  className="flex items-center gap-1 px-2 py-1 -ml-2 rounded-lg text-[11px] text-base-400 hover:text-base-200 hover:bg-base-800 transition-colors"
                >
                  <ChevronDown size={12} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
                  Models included
                </button>
              )}
              <button
                onClick={() => setLearnMoreProfile(profile)}
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-accent hover:text-accent/80 hover:bg-accent/10 transition-colors ml-auto"
              >
                <CircleHelp size={12} />
                Learn more
              </button>
            </div>

            <AnimatePresence>
              {expanded && summary.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden"
                >
                  <div className="px-4 pb-3 space-y-1">
                    {summary.map((m) => (
                      <div
                        key={m.modelId}
                        className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-base-900/50 border border-base-700/50"
                      >
                        <span className="text-[11px] text-base-200 font-medium">{m.displayName}</span>
                        <span className="text-[10px] text-base-500">{m.usedFor.join(', ')}</span>
                      </div>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )
      })}

      {learnMoreProfile && (
        <LearnMoreModal profile={learnMoreProfile} onClose={() => setLearnMoreProfile(null)} />
      )}
    </div>
  )
}

function LearnMoreModal({ profile, onClose }: { profile: PerformanceProfile; onClose: () => void }) {
  const Icon = PROFILE_ICON[profile.id]
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <motion.div
        role="dialog"
        aria-modal="true"
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.15 }}
        className="w-[420px] rounded-2xl border border-base-700 bg-base-900 p-5 shadow-panel"
      >
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-accent/15 flex items-center justify-center">
              <Icon size={16} className="text-accent" />
            </div>
            <p className="text-sm font-semibold text-base-100">{profile.label}</p>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors">
            <X size={15} />
          </button>
        </div>
        <div className="space-y-3">
          <LearnMoreRow title="What changes" text={profile.learnMore.whatChanges} />
          <LearnMoreRow title="Why it exists" text={profile.learnMore.whyItExists} />
          <LearnMoreRow title="Who should use it" text={profile.learnMore.whoShouldUse} />
          <LearnMoreRow title="Hardware" text={profile.learnMore.hardware} />
        </div>
        <button
          onClick={onClose}
          className="w-full mt-4 py-1.5 rounded-lg text-sm font-medium bg-accent hover:bg-accent/90 text-white transition-colors"
        >
          Got it
        </button>
      </motion.div>
    </div>
  )
}

function LearnMoreRow({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 mb-0.5">{title}</p>
      <p className="text-xs text-base-300 leading-relaxed">{text}</p>
    </div>
  )
}
