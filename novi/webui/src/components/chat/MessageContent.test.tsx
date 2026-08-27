import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { MessageContent, preprocessContent } from './MessageContent'

function renderContent(content: string) {
  return render(<MessageContent content={content} />)
}

// Helper to check raw LaTeX delimiters not visible
function html(container: HTMLElement) {
  return container.innerHTML
}

describe('MessageContent — Markdown rendering', () => {
  it('renders plain text', () => {
    const { container } = renderContent('Hello, Novi!')
    expect(container.textContent).toContain('Hello, Novi!')
  })

  it('renders bold text without raw **', () => {
    const { container } = renderContent('This is **bold** text')
    expect(container.innerHTML).not.toContain('**')
    const strong = container.querySelector('strong')
    expect(strong).not.toBeNull()
    expect(strong?.textContent).toBe('bold')
  })

  it('renders italic text', () => {
    const { container } = renderContent('This is *italic* text')
    expect(container.innerHTML).not.toContain('*italic*')
    const em = container.querySelector('em')
    expect(em).not.toBeNull()
  })

  it('renders ordered list', () => {
    const { container } = renderContent('1. First\n2. Second\n3. Third')
    const ol = container.querySelector('ol')
    expect(ol).not.toBeNull()
    expect(container.querySelectorAll('li')).toHaveLength(3)
  })

  it('renders unordered list', () => {
    const { container } = renderContent('- Apple\n- Banana\n- Cherry')
    const ul = container.querySelector('ul')
    expect(ul).not.toBeNull()
    expect(container.querySelectorAll('li')).toHaveLength(3)
  })

  it('renders nested list', () => {
    const { container } = renderContent('- Parent\n  - Child\n  - Child 2\n- Parent 2')
    expect(container.querySelectorAll('li').length).toBeGreaterThanOrEqual(4)
    // nested ul inside li
    expect(container.querySelector('li ul') || container.querySelector('ul ul')).not.toBeNull()
  })

  it('renders inline code', () => {
    const { container } = renderContent('Use `const x = 1` please')
    expect(container.innerHTML).not.toContain('`const')
    const code = container.querySelector('code')
    expect(code).not.toBeNull()
    expect(code?.textContent).toContain('const x = 1')
  })

  it('renders Python code block', () => {
    const { container } = renderContent('```python\nprint("Hello, Novi!")\n```')
    expect(container.textContent).toContain('Hello, Novi!')
    expect(container.innerHTML).not.toContain('```python')
    // language label
    expect(container.textContent).toContain('python')
    // copy button
    expect(container.textContent).toContain('Copy')
  })

  it('renders JavaScript/TypeScript code block', () => {
    const { container } = renderContent('```typescript\nconst x: number = 42;\n```')
    expect(container.textContent).toContain('const x')
    expect(container.textContent).toContain('typescript')
  })

  it('renders markdown table', () => {
    const md = '| Name | Value |\n|---|---|\n| A | 1 |\n| B | 2 |'
    const { container } = renderContent(md)
    const table = container.querySelector('table')
    expect(table).not.toBeNull()
    expect(container.querySelector('th')?.textContent).toBe('Name')
    expect(container.querySelectorAll('td')).toHaveLength(4)
    // Should be wrapped in overflow container
    expect(container.innerHTML).toContain('overflow-x-auto')
  })

  it('renders blockquote', () => {
    const { container } = renderContent('> This is a quote')
    const bq = container.querySelector('blockquote')
    expect(bq).not.toBeNull()
    expect(bq?.textContent).toContain('This is a quote')
  })

  it('renders link correctly', () => {
    const { container } = renderContent('[Novi](https://example.com)')
    const a = container.querySelector('a')
    expect(a).not.toBeNull()
    expect(a?.href).toContain('example.com')
    expect(a?.textContent).toBe('Novi')
    expect(a?.getAttribute('target')).toBe('_blank')
    expect(a?.getAttribute('rel')).toContain('noopener')
    expect(container.innerHTML).not.toContain('[Novi](https://example.com)')
  })

  it('renders heading hierarchy', () => {
    const { container } = renderContent('# H1\n## H2\n### H3')
    expect(container.querySelector('h1')?.textContent).toBe('H1')
    expect(container.querySelector('h2')?.textContent).toBe('H2')
    expect(container.querySelector('h3')?.textContent).toBe('H3')
  })

  it('renders horizontal rule', () => {
    const { container } = renderContent('---')
    expect(container.querySelector('hr')).not.toBeNull()
  })
})

describe('MessageContent — Math rendering', () => {
  it('renders inline math with $ delimiters', () => {
    const { container } = renderContent('The answer is $\\ln(1)=0$.')
    // Should have KaTeX rendered, no raw $ visible
    expect(container.querySelector('.katex')).not.toBeNull()
    expect(html(container)).not.toContain('$\\ln(1)=0$')
    expect(html(container)).not.toContain('$ln(1)=0$')
  })

  it('renders inline math with \\( \\) delimiters', () => {
    const { container } = renderContent('The answer is \\(\\ln(1)=0\\).')
    expect(container.querySelector('.katex')).not.toBeNull()
    expect(html(container)).not.toContain('\\(\\ln')
  })

  it('renders block math with $$', () => {
    const { container } = renderContent('$$\n\\ln(1)=0\n$$')
    expect(container.querySelector('.katex-display')).not.toBeNull()
    expect(html(container)).not.toContain('$$')
  })

  it('renders block math with \\[ \\]', () => {
    const { container } = renderContent('\\[\\ln(1)=0\\]')
    expect(container.querySelector('.katex-display')).not.toBeNull()
    expect(html(container)).not.toContain('\\[')
  })

  it('renders mixed response with inline and block math', () => {
    const md = 'The natural logarithm of 1 is $\\ln(1)$.\n\n$$\n\\ln(1)=0\n$$\n\nTherefore the answer is 0.'
    const { container } = renderContent(md)
    // At least one inline and one block
    expect(container.querySelector('.katex')).not.toBeNull()
    expect(container.querySelector('.katex-display')).not.toBeNull()
    expect(container.textContent).toContain('Therefore the answer is 0')
  })

  it('preserves currency $20 as text, not math', () => {
    const { container } = renderContent('The item costs $20.')
    expect(container.querySelector('.katex')).toBeNull()
    expect(container.textContent).toContain('$20')
    expect(container.textContent).toContain('The item costs $20.')
  })

  it('does not convert $20 and $30 pair into math', () => {
    const { container } = renderContent('The item costs $20 and $30 total.')
    expect(container.querySelector('.katex')).toBeNull()
    expect(container.textContent).toContain('$20')
    expect(container.textContent).toContain('$30')
  })

  it('handles the exact acceptance case: ($\\ln(1)$)', () => {
    const { container } = renderContent('The natural logarithm of 1 ($\\ln(1)$) is 0.')
    // No raw delimiters
    expect(html(container)).not.toContain('$\\ln(1)$')
    expect(html(container)).not.toContain('($\\ln')
    // KaTeX should exist
    expect(container.querySelector('.katex')).not.toBeNull()
    // Text around math preserved
    expect(container.textContent).toContain('The natural logarithm of 1')
    expect(container.textContent).toContain('is 0')
    // No raw markdown
    expect(html(container)).not.toContain('**')
  })

  it('renders inline math inside parentheses with outer text parentheses preserved', () => {
    const { container } = renderContent('Value is ($x^2$) here')
    expect(container.querySelector('.katex')).not.toBeNull()
    // Parentheses should still be in text
    expect(container.textContent).toContain('(')
    expect(container.textContent).toContain(')')
  })
})

describe('MessageContent — Security', () => {
  it('does not execute script tags from model output', () => {
    const { container } = renderContent('<script>alert("xss")</script> Hello')
    expect(container.innerHTML).not.toContain('<script>')
    // Should be escaped as text
    expect(container.textContent).toContain('Hello')
  })

  it('escapes raw HTML', () => {
    const { container } = renderContent('<img src=x onerror=alert(1)>')
    // Should not create actual img element (no rehype-raw)
    const img = container.querySelector('img')
    expect(img).toBeNull()
    // Raw HTML should be escaped, not rendered as tag
    expect(container.innerHTML).toContain('&lt;img')
    expect(container.innerHTML).not.toContain('<img src=x')
  })
})

describe('MessageContent — Streaming / incomplete syntax', () => {
  it('handles incomplete markdown bold during streaming', () => {
    const { container } = renderContent('This is **bold')
    // Should not crash, should render something
    expect(container.textContent).toContain('bold')
    expect(() => renderContent('This is **bold')).not.toThrow()
  })

  it('handles incomplete inline math during streaming', () => {
    const { container } = renderContent('$$ \\ln(1')
    expect(() => renderContent('$$ \\ln(1')).not.toThrow()
    // Incomplete should remain as text with ln(1 visible, not empty katex
    // After our protectIncompleteMath, it should be text, so no katex-display
    expect(container.querySelector('.katex-display')).toBeNull()
    // Text should still be visible (escaped $$)
    expect(container.textContent).toContain('ln(1')
  })

  it('handles incomplete $ math during streaming', () => {
    const { container } = renderContent('The value is $\\ln(1')
    expect(() => renderContent('The value is $\\ln(1')).not.toThrow()
    expect(container.textContent).toContain('ln(1')
  })

  it('handles incomplete code block during streaming', () => {
    const { container } = renderContent('```python\nprint("hello"')
    expect(() => renderContent('```python\nprint("hello"')).not.toThrow()
    expect(container.textContent).toContain('print')
  })

  it('handles incomplete LaTeX with \\( during streaming', () => {
    const { container } = renderContent('\\(\\ln(1')
    expect(() => renderContent('\\(\\ln(1')).not.toThrow()
  })

  it('recovers from incomplete to complete', () => {
    const incomplete = renderContent('$$ \\ln(1')
    expect(incomplete.container.querySelector('.katex-display')).toBeNull()
    const complete = renderContent('$$\n\\ln(1)=0\n$$')
    expect(complete.container.querySelector('.katex-display')).not.toBeNull()
  })

  it('does not flicker excessively: renders stable keys', () => {
    // Just ensures no React key warnings for sequential renders
    const { rerender } = render(<MessageContent content="Hello" />)
    expect(() => rerender(<MessageContent content="Hello world" />)).not.toThrow()
    expect(() => rerender(<MessageContent content="Hello world $$ x=1 $$" />)).not.toThrow()
  })
})

describe('preprocessContent — currency & delimiter logic', () => {
  it('normalizes \\( \\) to $ $', () => {
    expect(preprocessContent('\\(x^2\\)')).toContain('$x^2$')
  })
  it('normalizes \\[ \\] to $$ $$', () => {
    const out = preprocessContent('\\[x^2\\]')
    expect(out).toContain('$$')
    expect(out).toContain('x^2')
  })
  it('protects $20 currency', () => {
    const out = preprocessContent('costs $20.')
    expect(out).toContain('\\$20')
  })
  it('does not protect $x$ math', () => {
    const out = preprocessContent('$x^2$')
    expect(out).toBe('$x^2$')
  })
  it('protects currency inside code blocks differently: no transform inside code', () => {
    const out = preprocessContent('`$20` and $20')
    expect(out).toContain('`$20`')
    expect(out).toContain('\\$20')
  })
  it('handles \\( \\) inside code: no conversion', () => {
    const out = preprocessContent('`\\(x\\)`')
    expect(out).toContain('`\\(x\\)`')
  })
})

describe('MessageContent — Complex acceptance (Phase 13)', () => {
  it('renders heading + paragraphs + inline math + block equation + bullet list + code block + table as unified UI', () => {
    const md = `# Analysis of ln(1)

The natural logarithm of 1 is $\\ln(1)$.

It satisfies the equation:

$$
\\ln(1)=0
$$

Key points:

- $\\ln(1)=0$ by definition
- $e^0 = 1$

\`\`\`python
import math
print(math.log(1))
\`\`\`

| Property | Value |
|---|---|
| ln(1) | 0 |
| e^0 | 1 |`

    const { container } = renderContent(md)
    expect(container.querySelector('h1')).not.toBeNull()
    expect(container.querySelector('.katex')).not.toBeNull()
    expect(container.querySelector('.katex-display')).not.toBeNull()
    expect(container.querySelector('ul')).not.toBeNull()
    expect(container.querySelector('table')).not.toBeNull()
    expect(container.textContent).toContain('import math')
    // No raw delimiters for complete math
    expect(html(container)).not.toContain('$\\ln(1)$')
  })
})

describe('Regression — Same-line display math (follow-up)', () => {
  it('1. Same-line $$...$$ → display KaTeX', () => {
    const { container } = renderContent('$$\\ln(1)=0$$')
    expect(container.querySelector('.katex-display')).not.toBeNull()
    expect(html(container)).not.toContain('$$')
  })

  it('2. Multiline $$...$$ → display KaTeX', () => {
    const { container } = renderContent('$$\n\\ln(1)=0\n$$')
    expect(container.querySelector('.katex-display')).not.toBeNull()
    expect(html(container)).not.toContain('$$')
  })

  it('3. $...$ → inline KaTeX (not display)', () => {
    const { container } = renderContent('$\\ln(1)$')
    expect(container.querySelector('.katex')).not.toBeNull()
    expect(container.querySelector('.katex-display')).toBeNull()
    expect(html(container)).not.toContain('$\\ln(1)$')
  })

  it('4. $20 → plain currency (no KaTeX)', () => {
    const { container } = renderContent('The item costs $20.')
    expect(container.querySelector('.katex')).toBeNull()
    expect(container.querySelector('.katex-display')).toBeNull()
    expect(container.textContent).toContain('$20')
  })

  it('5. Incomplete $$... during streaming → safe text rendering', () => {
    const { container } = renderContent('$$\\ln(1)=0')
    expect(() => renderContent('$$\\ln(1)=0')).not.toThrow()
    expect(container.querySelector('.katex-display')).toBeNull()
    // Should show raw-like text, not empty display
    expect(container.textContent).toContain('\\ln(1)=0')
  })

  it('preserves $20 and $$ display math together', () => {
    const { container } = renderContent('The item costs $20. Equation: $$\\ln(1)=0$$')
    expect(container.textContent).toContain('$20')
    expect(container.querySelector('.katex-display')).not.toBeNull()
  })

  it('does not treat $$ inside code block as math', () => {
    const { container } = renderContent('```\n$$\\ln(1)=0$$\n```')
    // Inside code block, should not render katex-display, should be code
    expect(container.querySelector('.katex-display')).toBeNull()
    expect(container.textContent).toContain('$$\\ln(1)=0$$')
  })
})
