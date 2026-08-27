import { useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Check, Copy } from 'lucide-react'

export function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false)

  const onCopy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="rounded-xl overflow-hidden border border-base-700 my-2.5 max-w-full">
      <div className="flex items-center justify-between px-3 py-1.5 bg-base-800 text-[11px] text-base-400">
        <span className="uppercase tracking-wide font-medium">{language}</span>
        <button
          onClick={onCopy}
          aria-label={copied ? 'Copied' : 'Copy code'}
          className="flex items-center gap-1 hover:text-base-100 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded px-1"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <div className="overflow-x-auto max-w-full">
        <SyntaxHighlighter
          language={language}
          style={oneDark}
          customStyle={{
            margin: 0,
            background: '#1c1c1c',
            fontSize: '13px',
            padding: '12px 14px',
            fontFamily: '"JetBrains Mono", ui-monospace, monospace',
            lineHeight: '1.6',
          }}
          wrapLongLines={false}
          PreTag="div"
        >
          {code}
        </SyntaxHighlighter>
      </div>
    </div>
  )
}
