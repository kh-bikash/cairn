import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Citation } from '../lib/types'

interface Props {
  text: string
  citations?: Citation[]
  onCite?: (c: Citation) => void
  className?: string
}

/** Markdown with numbered citation chips: `[3]` becomes a chip that opens source 3. */
export function Markdown({ text, citations = [], onCite, className }: Props) {
  const byRef = new Map(citations.map((c) => [c.ref, c]))
  const linked = text.replace(/\[(\d{1,3})\]/g, (m, n) => (byRef.has(Number(n)) ? `[${n}](#cite-${n})` : m))
  return (
    <div className={`prose-cairn text-[15px] text-fg ${className ?? ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith('#cite-')) {
              const c = byRef.get(Number(href.slice(6)))
              if (!c) return <>{children}</>
              return (
                <button
                  type="button"
                  onClick={() => onCite?.(c)}
                  title={`${c.title}${c.quote ? `\n\n“${c.quote}”` : ''}`}
                  className="mx-0.5 inline-flex h-[1.2rem] min-w-[1.2rem] -translate-y-px items-center justify-center rounded-md bg-accent-soft px-1 align-middle font-mono text-[10.5px] font-semibold text-accent hover:opacity-80"
                >
                  {c.ref}
                </button>
              )
            }
            return (
              <a href={href} target={href?.startsWith('#') ? undefined : '_blank'} rel="noreferrer">
                {children as ReactNode}
              </a>
            )
          },
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  )
}
