import { motion } from 'framer-motion'
import { Check, X, FileText } from 'lucide-react'
import { PlanData } from '@/types'

export function InlinePlanApproval({
  plan,
  onApprove,
  onReject,
}: {
  plan: PlanData
  onApprove?: () => void
  onReject?: () => void
}) {
  if (plan.status === 'pending') {
    return (
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 overflow-hidden"
      >
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-emerald-500/10">
          <FileText size={14} className="text-emerald-400 shrink-0" />
          <span className="text-[13px] font-medium text-emerald-300">Proposed Plan</span>
        </div>
        <div className="px-3 py-2.5 text-[12px] text-base-300 whitespace-pre-wrap font-mono leading-relaxed max-h-[300px] overflow-y-auto">
          {plan.plan}
        </div>
        <div className="flex gap-2 px-3 pb-3">
          <button
            onClick={onApprove}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 py-2 text-[13px] font-medium text-emerald-300 transition-colors"
          >
            <Check size={14} />
            Approve
          </button>
          <button
            onClick={onReject}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 py-2 text-[13px] font-medium text-red-400 transition-colors"
          >
            <X size={14} />
            Reject
          </button>
        </div>
      </motion.div>
    )
  }

  if (plan.status === 'approved') {
    return (
      <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5 flex items-center gap-2.5">
        <Check size={14} className="text-emerald-400 shrink-0" />
        <span className="text-[13px] text-emerald-300">Plan approved — executing...</span>
      </div>
    )
  }

  return null
}
