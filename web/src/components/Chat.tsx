import {
  AlertTriangle,
  ArrowUp,
  Boxes,
  Clock,
  Lightbulb,
  Loader2,
  Network,
  Route,
  Search,
  Square,
  TrendingDown,
  User,
  Users,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Citation, Step, Turn } from '../types'
import { ActionCard } from './Actions'

const TOOL_META: Record<string, { icon: LucideIcon; verb: string }> = {
  search: { icon: Search, verb: 'Searching' },
  why: { icon: Lightbulb, verb: 'Tracing decisions' },
  who_knows: { icon: Users, verb: 'Ranking experts' },
  impact: { icon: Zap, verb: 'Following dependencies' },
  bus_factor: { icon: AlertTriangle, verb: 'Measuring knowledge risk' },
  person_profile: { icon: User, verb: 'Profiling' },
  timeline: { icon: Clock, verb: 'Reading history' },
  connect: { icon: Route, verb: 'Finding a path' },
  drift_report: { icon: TrendingDown, verb: 'Checking for drift' },
  teams: { icon: Network, verb: 'Detecting teams' },
  list_systems: { icon: Boxes, verb: 'Listing systems' },
  suggest_owners: { icon: Users, verb: 'Finding owners' },
  details: { icon: Search, verb: 'Opening' },
  propose_wiki_update: { icon: Boxes, verb: 'Drafting wiki update' },
  propose_issue_routing: { icon: Users, verb: 'Drafting issue routing' },
}

export const SUGGESTIONS = [
  'Why did falkordb-py add the hiredis extra?',
  'Who knows GraphRAG-SDK ingestion best?',
  'What breaks if falkordb-py changes?',
  'What happens if galshubeli leaves?',
  'Which decisions are no longer valid?',
  'What changed in falkordb/asyncio since August?',
]

interface Props {
  turns: Turn[]
  busy: boolean
  onAsk: (q: string) => void
  onStop: () => void
  onCite: (turn: Turn, c: Citation) => void
  onStep: (step: Step) => void
}

export function Chat({ turns, busy, onAsk, onStop, onCite, onStep }: Props) {
  const [q, setQ] = useState('')
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, which React would treat as a cleanup fn.
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  const submit = (e?: FormEvent) => {
    e?.preventDefault()
    const text = q.trim()
    if (!text || busy) return
    onAsk(text)
    setQ('')
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {turns.length === 0 && <Welcome onAsk={onAsk} />}
        {turns.map((t) => (
          <TurnView key={t.id} turn={t} onCite={(c) => onCite(t, c)} onStep={onStep} />
        ))}
        <div ref={end} />
      </div>
      <form onSubmit={submit} className="border-t border-ink-700 p-3">
        <div className="flex items-end gap-2 rounded-xl border border-ink-600 bg-ink-800 p-2 focus-within:border-teal-400/60">
          <textarea
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
            }}
            rows={1}
            placeholder="Ask why, who knows, what breaks…"
            className="max-h-32 min-h-[2.25rem] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
            aria-label="Question"
          />
          {busy ? (
            <button type="button" onClick={onStop} className="grid h-9 w-9 place-items-center rounded-lg bg-ink-600 text-slate-200 hover:bg-ink-700" aria-label="Stop">
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button type="submit" disabled={!q.trim()} className="grid h-9 w-9 place-items-center rounded-lg bg-teal-400 text-ink-950 transition disabled:opacity-30" aria-label="Ask">
              <ArrowUp className="h-4 w-4" />
            </button>
          )}
        </div>
      </form>
    </div>
  )
}

function Welcome({ onAsk }: { onAsk: (q: string) => void }) {
  return (
    <div className="space-y-4 pt-2">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Ask the company brain</h2>
        <p className="mt-1 text-sm leading-relaxed text-slate-400">
          Every answer is a path through the graph: decisions, the reasons behind them, and the PRs, issues and people that back each step.
        </p>
      </div>
      <div className="grid gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onAsk(s)}
            className="rounded-lg border border-ink-600 bg-ink-800/60 px-3 py-2 text-left text-sm text-slate-300 transition hover:border-teal-400/50 hover:text-slate-100"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

function TurnView({ turn, onCite, onStep }: { turn: Turn; onCite: (c: Citation) => void; onStep: (s: Step) => void }) {
  return (
    <div className="space-y-2.5">
      <div className="ml-8 rounded-xl rounded-tr-sm bg-teal-400/10 px-3 py-2 text-sm text-teal-50 ring-1 ring-teal-400/20">{turn.question}</div>
      {turn.steps.length > 0 && (
        <ol className="space-y-1">
          {turn.steps.map((s) => (
            <StepRow key={s.id} step={s} onClick={() => onStep(s)} />
          ))}
        </ol>
      )}
      {turn.steps
        .filter((s) => s.action)
        .map((s) => (
          <ActionCard key={s.action!.uid} action={s.action!} />
        ))}
      {turn.notices.map((n) => (
        <p key={n} className="rounded-md bg-amber-400/10 px-2.5 py-1.5 text-xs text-amber-200">
          {n}
        </p>
      ))}
      {turn.pending && !turn.answer && (
        <p className="flex items-center gap-2 text-xs text-slate-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Thinking over the graph…
        </p>
      )}
      {turn.answer && <Answer text={turn.answer.text} citations={turn.answer.citations} onCite={onCite} />}
      {turn.answer && (
        <p className="text-[11px] text-slate-500">
          {turn.answer.mode === 'router' ? 'Answered directly from the graph (no LLM)' : 'Agent answer'} · {turn.answer.citations.length} sources
          {turn.seconds != null && ` · ${turn.seconds.toFixed(1)}s`}
        </p>
      )}
      {turn.error && <p className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-xs text-red-300">{turn.error}</p>}
    </div>
  )
}

function StepRow({ step, onClick }: { step: Step; onClick: () => void }) {
  const meta = TOOL_META[step.name] ?? { icon: Search, verb: step.name }
  const Icon = meta.icon
  const arg = Object.values(step.args)[0]
  return (
    <li>
      <button
        onClick={onClick}
        className={`flex w-full items-start gap-2 rounded-md px-2 py-1 text-left text-xs transition hover:bg-ink-700/60 ${step.error ? 'text-red-300' : 'text-slate-400'}`}
      >
        {step.summary == null ? <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" /> : <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-300" />}
        <span>
          <span className="text-slate-300">{meta.verb}</span>
          {arg != null && <span className="text-slate-500"> · {String(arg)}</span>}
          {step.summary && <span className="block text-slate-500">{step.summary}</span>}
        </span>
      </button>
    </li>
  )
}

function Answer({ text, citations, onCite }: { text: string; citations: Citation[]; onCite: (c: Citation) => void }) {
  const byRef = new Map(citations.map((c) => [c.ref, c]))
  const linked = text.replace(/\[(\d{1,3})\]/g, (m, n) => (byRef.has(Number(n)) ? `[${n}](#cite-${n})` : m))
  return (
    <div className="answer text-sm leading-relaxed text-slate-200">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith('#cite-')) {
              const c = byRef.get(Number(href.slice(6)))
              return c ? <CiteChip c={c} onClick={() => onCite(c)} /> : <>{children}</>
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-teal-300 underline-offset-2 hover:underline">
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

function CiteChip({ c, onClick }: { c: Citation; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={`${c.title}${c.quote ? `\n\n“${c.quote}”` : ''}`}
      className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-px items-center justify-center rounded bg-teal-400/15 px-1 align-middle font-mono text-[10px] font-semibold text-teal-200 ring-1 ring-teal-400/30 transition hover:bg-teal-400/30"
    >
      {c.ref}
    </button>
  )
}
