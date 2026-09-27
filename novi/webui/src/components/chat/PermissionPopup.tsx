import { motion, AnimatePresence } from "framer-motion"
import { X } from "lucide-react"
import { createPortal } from "react-dom"

import type { PermissionRequest } from '@/hooks/useNoviChat'

interface PermissionPopupProps {
  request: PermissionRequest
  onAllow: () => void
  onDeny: () => void
  onCancel: () => void
}

function getToolIcon(name: string) {
  const n = name.toLowerCase()
  if (n.includes("search") || n.includes("web")) return "🔍"
  if (n.includes("file") || n.includes("read") || n.includes("write") || n.includes("edit") || n.includes("glob") || n.includes("grep")) return "📄"
  if (n.includes("shell") || n.includes("terminal") || n.includes("run") || n.includes("execute") || n.includes("command")) return "💻"
  if (n.includes("skill") || n.includes("activate")) return "✨"
  if (n.includes("progress") || n.includes("report")) return "📊"
  return "🧠"
}

function getToolLabel(name: string): string {
  const n = name.toLowerCase()
  if (n === "search_knowledge") return "Search Novi knowledge"
  if (n === "web_search" || n === "web_search_pipeline") return "Search the web"
  if (n === "web_fetch") return "Fetch web page"
  if (n === "read_file") return "Read file"
  if (n === "write_file") return "Write file"
  if (n === "edit_file") return "Edit file"
  if (n === "list_directory") return "List directory"
  if (n === "glob_search") return "Find files"
  if (n === "grep") return "Search code"
  if (n === "shell" || n === "run_command") return "Run command"
  if (n === "report_progress") return "Progress update"
  if (n === "activate_skill") return "Activate skill"
  if (n === "calculator") return "Calculate"
  if (n === "current_time") return "Get time"
  if (n === "read_knowledge") return "Read knowledge"
  if (n === "write_knowledge") return "Write knowledge"
  return name.replace(/_/g, " ")
}

function formatArgs(args: Record<string, unknown>): string {
  if (!args || Object.keys(args).length === 0) return ""
  const entries = Object.entries(args)
    .filter(([, v]) => v !== undefined && v !== "")
    .slice(0, 3)
  if (entries.length === 0) return ""
  return entries.map(([k, v]) => k + ": " + String(v).slice(0, 60)).join(", ")
}

export function PermissionPopup({ request, onAllow, onDeny, onCancel }: PermissionPopupProps) {
  const toolLabel = getToolLabel(request.tool)
  const toolIcon = getToolIcon(request.tool)
  const argsText = formatArgs(request.args)
  const effects = request.effects || []

  return createPortal(
    <AnimatePresence>
      <motion.div
        initial={{opacity: 0}}
        animate={{opacity: 1}}
        exit={{opacity: 0}}
        className="fixed inset-0 z-50 flex items-center justify-center px-4 py-8"
        role="dialog"
        aria-modal="true"
        aria-labelledby="permission-title"
        aria-describedby="permission-desc"
      >
        {/* Backdrop */}
        <motion.div
          initial={{opacity: 0}}
          animate={{opacity: 1}}
          exit={{opacity: 0}}
          className="absolute inset-0 bg-black/60 backdrop-blur-sm"
          onClick={onCancel}
        />

        {/* Popup */}
        <motion.div
          initial={{opacity: 0, scale: 0.95, y: 20}}
          animate={{opacity: 1, scale: 1, y: 0}}
          exit={{opacity: 0, scale: 0.95, y: -20}}
          transition={{duration: 0.15, ease: [0.22, 1, 0.36, 1]}}
          className="relative w-full max-w-md bg-base-900 border border-base-700 rounded-xl shadow-2xl overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-center gap-3 px-5 py-4 border-b border-base-700/50 bg-base-950/80">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg shrink-0 bg-accent/20 text-accent">
              {toolIcon}
            </div>
            <div className="flex-1 min-w-0">
              <h2 id="permission-title" className="font-semibold text-base-100 truncate">
                Permission Required
              </h2>
              <p id="permission-desc" className="text-[12px] text-base-500 truncate">
                Novi wants to run <code className="bg-base-800 px-1.5 py-0.5 rounded text-accent font-mono text-[11px]">{request.tool}</code>
              </p>
            </div>
            <button
              onClick={onCancel}
              className="p-1.5 rounded-lg text-base-500 hover:text-base-200 hover:bg-base-800/50 transition-colors"
              aria-label="Dismiss"
            >
              <X size={18} />
            </button>
          </div>

          {/* Body */}
          <div className="px-5 py-4 space-y-4">
            {/* Tool info */}
            <div className="bg-base-950/50 border border-base-700/50 rounded-lg p-3 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-base-400">Action</span>
                <span className="font-mono text-[12px] text-base-200">{toolLabel}</span>
              </div>
              {argsText && (
                <div className="flex items-start gap-2">
                  <span className="text-[11px] font-medium text-base-400 shrink-0 mt-0.5">Arguments</span>
                  <span className="font-mono text-[11px] text-base-500 truncate">{argsText}</span>
                </div>
              )}
              {effects.length > 0 && (
                <div>
                  <span className="text-[11px] font-medium text-base-400">Effects</span>
                  <div className="flex flex-wrap gap-1.5 mt-1.5">
                    {effects.map((effect, i) => (
                      <span key={i} className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-base-800/50 text-base-400 border border-base-700/50">
                        {effect}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {typeof request.proposedDiff === 'object' && request.proposedDiff !== null && (
                <div className="border-t border-base-700/50 pt-3">
                  <span className="text-[11px] font-medium text-base-400">Changes</span>
                  <pre className="mt-2 p-2 bg-base-950 border border-base-700/50 rounded text-[10px] font-mono text-base-400 overflow-x-auto max-h-32 overflow-y-auto">
                    {JSON.stringify(request.proposedDiff, null, 2).slice(0, 500)}
                  </pre>
                </div>
              )}
            </div>

          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-base-700/50 bg-base-950/80">
            <button
              onClick={onCancel}
              className="px-4 py-2 text-[13px] font-medium text-base-300 hover:text-base-100 hover:bg-base-800/50 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={onDeny}
              className="px-4 py-2 text-[13px] font-medium text-base-300 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors flex items-center gap-1.5"
            >
              <span>X</span>
              Deny
            </button>
            <button
              onClick={onAllow}
              className="px-4 py-2 text-[13px] font-medium rounded-lg transition-colors flex items-center gap-1.5 bg-accent text-base-950 hover:bg-accent/90"
            >
              <span>V</span>
              Allow
            </button>
          </div>
        </motion.div>
      </motion.div>
      </AnimatePresence>,
      document.body
    )
  }
