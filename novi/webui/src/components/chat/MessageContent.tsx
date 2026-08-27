import { memo, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { CodeBlock } from './CodeBlock'

// ------------------------------------------------------------
// Preprocessing helpers
// ------------------------------------------------------------

/**
 * Protect inline code and fenced code blocks from transforms.
 * Splits content into code vs non-code segments.
 */
function withCodeProtection(
  content: string,
  fn: (nonCode: string) => string
): string {
  // Regex matches: fenced blocks ```...``` and inline `...`
  // We treat them as opaque.
  const codeRegex = /(```[\s\S]*?```|`[^`]*`)/g
  const parts: string[] = []
  let lastIndex = 0
  let m: RegExpExecArray | null
  // Collect placeholders
  const placeholders: string[] = []
  let protectedContent = ''
  // Build array of segments
  const segments: Array<{ text: string; isCode: boolean }> = []
  let idx = 0
  while ((m = codeRegex.exec(content)) !== null) {
    if (m.index > idx) {
      segments.push({ text: content.slice(idx, m.index), isCode: false })
    }
    segments.push({ text: m[0], isCode: true })
    idx = m.index + m[0].length
  }
  if (idx < content.length) {
    segments.push({ text: content.slice(idx), isCode: false })
  }
  if (segments.length === 0) segments.push({ text: content, isCode: false })

  return segments
    .map(s => (s.isCode ? s.text : fn(s.text)))
    .join('')
}

/**
 * Convert \(...\) -> $...$  and  \[...\] -> $$...$$
 * Only converts balanced pairs to avoid streaming flicker.
 * Block form uses newlines so remark-math treats it as display math.
 */
function normalizeLatexDelimiters(text: string): string {
  // Block: \[ ... \] -> $$\n...\n$$
  text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_: string, inner: string) => `$$\n${inner}\n$$`)
  // Inline: \( ... \)
  text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_: string, inner: string) => `$${inner}$`)
  return text
}

/**
 * Ensure all valid $$...$$ expressions are treated as block/display math.
 * remark-math only treats $$ as display when on its own lines; same-line
 * $$...$$ would otherwise be inline. We normalize every complete $$...$$
 * pair to $$\n<content>\n$$ so single-line and multiline both become display.
 * Code blocks are already protected by withCodeProtection, so this runs only
 * on non-code segments. Incomplete $$ (no closing) is left untouched for
 * protectIncompleteMath to handle.
 */
function normalizeBlockMath(text: string): string {
  return text.replace(/\$\$([\s\S]*?)\$\$/g, (_: string, inner: string) => {
    const trimmed = inner.trim()
    // Empty $$ $$ (no content) — leave as is to avoid creating empty display
    if (trimmed === '') return `$$${inner}$$`
    return `$$\n${trimmed}\n$$`
  })
}

/**
 * Protect incomplete math delimiters during streaming.
 * If there's an unclosed $$ or $, escape the trailing delimiter so it renders as text
 * until the closing delimiter arrives. Prevents empty katex-display and flicker.
 */
function protectIncompleteMath(text: string): string {
  // Handle $$ first (block)
  const blockMatches = text.match(/\$\$/g)
  if (blockMatches && blockMatches.length % 2 === 1) {
    const lastIdx = text.lastIndexOf('$$')
    if (lastIdx !== -1) {
      text = text.slice(0, lastIdx) + '\\$\\$' + text.slice(lastIdx + 2)
    }
  }
  // Handle inline $ (single dollar) not part of $$ and not escaped
  let count = 0
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '$') {
      // skip if part of $$ (already removed) — check neighbours
      const isEscaped = i > 0 && text[i - 1] === '\\'
      const isPartOfBlock = (i + 1 < text.length && text[i + 1] === '$') || (i > 0 && text[i - 1] === '$')
      if (!isEscaped && !isPartOfBlock) count++
    }
  }
  // If single dollars count is odd, the last one is unclosed
  if (count % 2 === 1) {
    // Find last single $ from end
    for (let i = text.length - 1; i >= 0; i--) {
      if (text[i] === '$') {
        const isEscaped = i > 0 && text[i - 1] === '\\'
        const isPartOfBlock = (i + 1 < text.length && text[i + 1] === '$') || (i > 0 && text[i - 1] === '$')
        if (!isEscaped && !isPartOfBlock) {
          text = text.slice(0, i) + '\\$' + text.slice(i + 1)
          break
        }
      }
    }
  }
  // Handle unclosed \( and \[ that were not converted (no closing)
  // If text ends with \( without \), escape it
  // Already handled by normalize only converting balanced, so trailing \( remains.
  // Escape trailing \( so it doesn't look like incomplete math delimiter?
  // Leave as is — it will render as text \( which is fine.
  return text
}

/**
 * Protect currency amounts like $20, $20.50, $1,000 from being parsed as math.
 * We escape the $ as \$ so remark-math ignores it.
 * Currency heuristic: $ followed by digits, optional commas/decimals, at word boundary,
 * not followed by letter (to keep $2x$ math intact).
 */
function protectCurrency(text: string): string {
  // Match $ + number pattern
  // Use negative lookahead for letter after number boundary? Actually we ensure
  // after numeric token, next char is not a letter. \b handles, but $2x would have
  // digit then letter without boundary, so \b would fail before x, so not matched.
  // For $20 -> digits then boundary -> matched.
  // For $20.50 -> matched.
  // For $1,000.00 -> matched.
  return text.replace(/\$(?=\d)/g, (match, offset, full) => {
    // Peek ahead: get numeric token after $
    const after = full.slice(offset + 1)
    const numMatch = after.match(/^(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?/)
    if (!numMatch) return match
    const num = numMatch[0]
    const afterNum = after.slice(num.length)
    // If immediately followed by a letter, it's likely math like $2x$ -> don't protect
    if (afterNum.length > 0 && /^[A-Za-z]/.test(afterNum)) return match
    // Also if number is part of larger math expression, e.g., $20$ where afterNum starts with $?
    // That would be "$20$" -> afterNum = "$" -> not letter, we would protect incorrectly.
    // Check if this dollar is opening of a math expression that closes with $.
    // Heuristic: look ahead for closing $ with math-like content. If content between
    // $ and next $ is just digits, we treat as two currency amounts, not math.
    // So protecting is correct (e.g., "$20 and $30" -> both protected, no math).
    // If math is genuinely "$20$" (number in math), protecting would break it,
    // but that's rare and currency protection is higher priority per spec.
    return '\\$'
  })
}

/**
 * Main preprocessing pipeline.
 * Applied to raw LLM output before handing to ReactMarkdown.
 */
function preprocessContent(raw: string): string {
  return withCodeProtection(raw, (segment) => {
    let s = normalizeLatexDelimiters(segment)
    s = normalizeBlockMath(s)
    s = protectCurrency(s)
    s = protectIncompleteMath(s)
    return s
  })
}

// ------------------------------------------------------------
// Markdown component mapping — Novi typography treatment
// ------------------------------------------------------------

const markdownComponents = {
  h1: ({ children }: any) => (
    <h1 className="text-[20px] font-semibold text-base-100 mt-5 mb-3 leading-tight tracking-tight first:mt-0 break-words">
      {children}
    </h1>
  ),
  h2: ({ children }: any) => (
    <h2 className="text-[18px] font-semibold text-base-100 mt-5 mb-2.5 leading-tight first:mt-0 break-words">
      {children}
    </h2>
  ),
  h3: ({ children }: any) => (
    <h3 className="text-[16px] font-semibold text-base-100 mt-4 mb-2 leading-snug first:mt-0 break-words">
      {children}
    </h3>
  ),
  h4: ({ children }: any) => (
    <h4 className="text-[15px] font-semibold text-base-100 mt-4 mb-2 leading-snug first:mt-0 break-words">
      {children}
    </h4>
  ),
  p: ({ children }: any) => (
    <p className="my-2.5 leading-[1.7] text-[15px] text-base-100 break-words first:mt-0 last:mb-0">
      {children}
    </p>
  ),
  strong: ({ children }: any) => (
    <strong className="font-semibold text-base-100">{children}</strong>
  ),
  em: ({ children }: any) => (
    <em className="italic text-base-100">{children}</em>
  ),
  a: ({ children, href }: any) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent-soft underline decoration-accent-soft/40 underline-offset-[3px] hover:text-accent hover:decoration-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded-sm break-words"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }: any) => (
    <blockquote className="my-3 border-l-[3px] border-base-600 pl-3.5 py-1 text-base-300 bg-base-800/30 rounded-r-lg italic break-words">
      {children}
    </blockquote>
  ),
  ul: ({ children }: any) => (
    <ul className="my-2.5 ml-1 list-disc list-outside space-y-1.5 pl-5 marker:text-base-500 text-[15px] leading-relaxed break-words">
      {children}
    </ul>
  ),
  ol: ({ children }: any) => (
    <ol className="my-2.5 ml-1 list-decimal list-outside space-y-1.5 pl-5 marker:text-base-500 marker:font-medium text-[15px] leading-relaxed break-words">
      {children}
    </ol>
  ),
  li: ({ children }: any) => (
    <li className="pl-1 leading-relaxed [&>ul]:mt-1.5 [&>ol]:mt-1.5 [&>p]:my-1">{children}</li>
  ),
  hr: () => <hr className="my-5 border-base-700" />,
  code({ className, children, ...props }: any) {
    const match = /language-(\w+)/.exec(className || '')
    const codeStr = String(children).replace(/\n$/, '')
    const isBlock = !!match || codeStr.includes('\n')
    if (isBlock && match) {
      return <CodeBlock language={match[1]} code={codeStr} />
    }
    if (isBlock) {
      // Fenced block without language — render as polished block but without language label
      return <CodeBlock language="text" code={codeStr} />
    }
    return (
      <code
        className="bg-base-800 px-1.5 py-0.5 rounded-md text-accent-soft text-[13.5px] font-mono break-words"
        {...props}
      >
        {children}
      </code>
    )
  },
  pre: ({ children }: any) => {
    // react-markdown wraps code blocks in <pre>; we handle via CodeBlock already.
    // If children is already a CodeBlock, unwrap pre styling.
    // If plain pre (no language), keep minimal wrapper.
    return <>{children}</>
  },
  table: ({ children }: any) => (
    <div className="my-3 w-full overflow-x-auto rounded-lg border border-base-700">
      <table className="w-full text-sm border-collapse min-w-[400px]">{children}</table>
    </div>
  ),
  thead: ({ children }: any) => <thead className="bg-base-800">{children}</thead>,
  tbody: ({ children }: any) => <tbody className="divide-y divide-base-800">{children}</tbody>,
  tr: ({ children }: any) => <tr className="border-b border-base-800 last:border-0">{children}</tr>,
  th: ({ children }: any) => (
    <th className="px-3 py-2 text-left text-[12.5px] font-semibold text-base-200 tracking-wide whitespace-nowrap">
      {children}
    </th>
  ),
  td: ({ children }: any) => (
    <td className="px-3 py-2 text-[13.5px] leading-relaxed text-base-200 align-top break-words max-w-[260px]">
      {children}
    </td>
  ),
}

interface MessageContentProps {
  content: string
  // For future streaming optimization: hint that content is still streaming
  streaming?: boolean
}

export const MessageContent = memo(function MessageContent({
  content,
  streaming: _streaming,
}: MessageContentProps) {
  const processed = useMemo(() => preprocessContent(content), [content])

  return (
    <div className="novi-markdown min-w-0 max-w-full break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={markdownComponents as any}
      >
        {processed}
      </ReactMarkdown>
    </div>
  )
})

// Export preprocess for testing
export { preprocessContent, normalizeLatexDelimiters, normalizeBlockMath, protectCurrency, withCodeProtection, protectIncompleteMath }
