import { ArrowUp, ChevronDown, ChevronRight, GitCompareArrows, Loader2, Network, Quote, Siren, Square, UserRoundMinus } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { ActionCard } from '../components/ActionCard'
import { GraphView } from '../components/GraphView'
import { Markdown } from '../components/Markdown'
import { Badge, Card, cx } from '../components/ui'
import { ask } from '../lib/api'
import { kindLabel, plural } from '../lib/format'
import { href, sourceHref } from '../lib/router'
import type { Theme } from '../lib/theme'
import type { AgentEvent, Citation, SubGraph, Turn, WorkspaceSummary } from '../lib/types'

const STEP_LABEL: Record<string, string> = {
  search: 'Searched the graph',
  why: 'Traced decisions',
  who_knows: 'Ranked experts',
  impact: 'Followed dependencies',
  bus_factor: 'Measured knowledge risk',
  person_profile: 'Read a profile',
  timeline: 'Read the history',
  connect: 'Found a path',
  drift_report: 'Checked for drift',
  teams: 'Looked at teams',
  list_systems: 'Listed systems',
  suggest_owners: 'Found likely owners',
  details: 'Opened a source',
  change_impact: 'Assessed change impact',
  incident_memory: 'Searched past incidents',
  handoff_plan: 'Drafted a handoff plan',
  propose_wiki_update: 'Drafted a wiki update',
  propose_issue_routing: 'Drafted issue routing',
}

let seq = 0
const merge = (a: SubGraph, b: SubGraph): SubGraph => {
  const nodes = new Map(a.nodes.map((n) => [n.id, n]))
  for (const n of b.nodes) nodes.set(n.id, { ...nodes.get(n.id), ...n, props: { ...nodes.get(n.id)?.props, ...n.props } })
  const edges = new Map(a.edges.map((e) => [e.id, e]))
  for (const e of b.edges) edges.set(e.id, e)
  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}

export function AskPage({ summary, workspaceName, theme }: { summary: WorkspaceSummary | null; workspaceName: string; theme: Theme }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  const update = useCallback((id: string, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t))), [])

  const onAsk = useCallback(
    async (question: string) => {
      abort.current?.abort()
      const controller = new AbortController()
      abort.current = controller
      const id = `t${++seq}`
      const history = turns
        .filter((t) => t.answer)
        .slice(-3)
        .flatMap((t) => [
          { role: 'user', content: t.question },
          { role: 'assistant', content: t.answer!.text },
        ])
      setTurns((ts) => [...ts, { id, question, steps: [], notices: [], graph: { nodes: [], edges: [] }, pending: true }])
      setBusy(true)
      const onEvent = (e: AgentEvent) => {
        switch (e.type) {
          case 'tool_call':
            update(id, (t) => ({ ...t, steps: [...t.steps, { id: e.id, name: e.name, args: e.args, nodeIds: [] }] }))
            break
          case 'tool_result':
            update(id, (t) => ({
              ...t,
              graph: e.name === 'search' ? t.graph : merge(t.graph, e.subgraph),
              steps: t.steps.map((s) => (s.id === e.id ? { ...s, summary: e.summary, error: e.error, action: e.action } : s)),
            }))
            break
          case 'notice':
            update(id, (t) => ({ ...t, notices: [...t.notices, e.message] }))
            break
          case 'answer':
            update(id, (t) => ({ ...t, answer: { text: e.text, citations: e.citations, mode: e.mode } }))
            break
          case 'error':
            update(id, (t) => ({ ...t, error: e.message }))
            break
          case 'done':
            update(id, (t) => ({ ...t, seconds: e.seconds }))
            break
        }
      }
      try {
        await ask(question, history, onEvent, controller.signal)
      } catch (err) {
        if (!controller.signal.aborted) update(id, (t) => ({ ...t, error: (err as Error).message }))
      } finally {
        update(id, (t) => ({ ...t, pending: false }))
        if (abort.current === controller) setBusy(false)
      }
    },
    [turns, update],
  )

  return (
    <div className="flex min-h-full flex-col">
      <div className="mx-auto w-full max-w-3xl flex-1 px-5 pb-6 pt-8 sm:px-8">
        {turns.length === 0 ? (
          <Welcome summary={summary} workspaceName={workspaceName} onAsk={onAsk} />
        ) : (
          <div className="space-y-10">
            {turns.map((t) => (
              <TurnView key={t.id} turn={t} theme={theme} />
            ))}
            <div ref={end} />
          </div>
        )}
      </div>
      <Composer busy={busy} onAsk={onAsk} onStop={() => abort.current?.abort()} />
    </div>
  )
}

function Welcome({ summary, workspaceName, onAsk }: { summary: WorkspaceSummary | null; workspaceName: string; onAsk: (q: string) => void }) {
  const flows = [
    { page: 'change', icon: GitCompareArrows, title: 'Before you change something', text: 'What breaks, which decisions it touches, who to tell.' },
    { page: 'incident', icon: Siren, title: 'When something breaks', text: 'Similar past incidents, what fixed them, stale runbooks.' },
    { page: 'handoff', icon: UserRoundMinus, title: 'When someone leaves', text: 'What only they know, and who should take it over.' },
  ]
  return (
    <div className="fade-up pt-6 sm:pt-12">
      <h1 className="text-3xl font-semibold tracking-tight text-fg sm:text-4xl">Ask why.</h1>
      <p className="mt-2 text-[15px] text-muted">
        {workspaceName} remembered as a graph
        {summary && (
          <>
            {': '}
            {plural(summary.people, 'person').replace('persons', 'people')}, {plural(summary.systems, 'system')}, {plural(summary.decisions, 'decision')} traced to their sources
            {summary.from && summary.to ? `, ${summary.from.slice(0, 7)} to ${summary.to.slice(0, 7)}` : ''}.
          </>
        )}
      </p>
      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        {flows.map((f) => (
          <a key={f.page} href={href(f.page)} className="group rounded-xl border border-line bg-surface p-4 transition hover:border-line-strong">
            <f.icon className="h-5 w-5 text-accent" />
            <p className="mt-3 text-sm font-medium text-fg">{f.title}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted">{f.text}</p>
          </a>
        ))}
      </div>
      {summary && summary.suggestions.length > 0 && (
        <div className="mt-8">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-faint">Try asking</p>
          <div className="flex flex-wrap gap-2">
            {summary.suggestions.map((s) => (
              <button key={s} onClick={() => onAsk(s)} className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-sm text-fg transition hover:border-accent hover:text-accent">
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function TurnView({ turn, theme }: { turn: Turn; theme: Theme }) {
  const [showSteps, setShowSteps] = useState(false)
  const [showGraph, setShowGraph] = useState(false)
  const [activeCite, setActiveCite] = useState<string | null>(null)
  const sourcesRef = useRef<HTMLDivElement>(null)

  const onCite = (c: Citation) => {
    setActiveCite(c.uid)
    sourcesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  return (
    <section className="fade-up">
      <h2 className="text-lg font-semibold leading-snug tracking-tight text-fg">{turn.question}</h2>
      {turn.steps.length > 0 && (
        <button onClick={() => setShowSteps((s) => !s)} className="mt-2 inline-flex items-center gap-1 text-xs text-muted hover:text-fg">
          {turn.pending && !turn.answer ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : showSteps ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          {turn.steps.map((s) => STEP_LABEL[s.name] ?? s.name).filter((v, i, a) => a.indexOf(v) === i).join(' · ')}
        </button>
      )}
      {showSteps && (
        <ol className="mt-2 space-y-1 border-l border-line pl-3">
          {turn.steps.map((s) => (
            <li key={s.id} className={cx('text-xs', s.error ? 'text-danger' : 'text-muted')}>
              <span className="text-fg">{STEP_LABEL[s.name] ?? s.name}</span>
              {Object.values(s.args)[0] != null && <span> · {String(Object.values(s.args)[0]).slice(0, 80)}</span>}
              {s.summary && <span className="text-faint"> → {s.summary}</span>}
            </li>
          ))}
        </ol>
      )}
      {turn.pending && !turn.answer && turn.steps.length === 0 && (
        <p className="mt-3 flex items-center gap-2 text-sm text-muted">
          <Loader2 className="h-4 w-4 animate-spin" /> Reading the graph…
        </p>
      )}
      {turn.notices.map((n) => (
        <p key={n} className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
          {n}
        </p>
      ))}
      {turn.answer && (
        <div className="mt-4">
          <Markdown text={turn.answer.text} citations={turn.answer.citations} onCite={onCite} />
        </div>
      )}
      {turn.steps
        .filter((s) => s.action)
        .map((s) => (
          <div key={s.action!.uid} className="mt-4">
            <ActionCard action={s.action!} />
          </div>
        ))}
      {turn.error && <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{turn.error}</p>}
      {turn.answer && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-faint">
          <span>{turn.answer.mode === 'router' ? 'Answered from the graph' : 'Answered by the agent'}</span>
          {turn.seconds != null && <span>· {turn.seconds.toFixed(1)}s</span>}
          {turn.graph.nodes.length > 0 && (
            <button onClick={() => setShowGraph((g) => !g)} className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-muted hover:bg-surface-2 hover:text-fg">
              <Network className="h-3.5 w-3.5" /> {showGraph ? 'Hide' : 'Show'} reasoning graph
            </button>
          )}
        </div>
      )}
      {showGraph && (
        <div className="mt-3">
          <GraphView graph={turn.graph} theme={theme} height={380} />
        </div>
      )}
      {turn.answer && turn.answer.citations.length > 0 && (
        <div ref={sourcesRef} className="mt-5">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-faint">Sources</p>
          <div className="grid gap-2">
            {turn.answer.citations.map((c) => (
              <SourceCard key={c.uid} c={c} active={activeCite === c.uid} />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function SourceCard({ c, active }: { c: Citation; active: boolean }) {
  const link = sourceHref(c.uid, c.url)
  return (
    <Card className={cx('p-3 transition', active && 'border-accent')}>
      <a href={link.href} target={link.external ? '_blank' : undefined} rel="noreferrer" className="flex items-start gap-2.5">
        <span className="mt-0.5 rounded-md bg-accent-soft px-1.5 font-mono text-[10.5px] font-semibold text-accent">{c.ref}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-fg hover:underline">{c.title}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-faint">
            <Badge>{kindLabel(c.kind)}</Badge>
            {c.date}
          </span>
        </span>
      </a>
      {c.quote && (
        <blockquote className="mt-2 flex gap-2 border-l-2 border-accent/50 pl-2.5 text-[13px] leading-relaxed text-muted">
          <Quote className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
          {c.quote}
        </blockquote>
      )}
    </Card>
  )
}

function Composer({ busy, onAsk, onStop }: { busy: boolean; onAsk: (q: string) => void; onStop: () => void }) {
  const [q, setQ] = useState('')
  const submit = (e?: FormEvent) => {
    e?.preventDefault()
    const text = q.trim()
    if (!text || busy) return
    onAsk(text)
    setQ('')
  }
  return (
    <div className="sticky bottom-0 border-t border-line bg-bg/90 backdrop-blur">
      <form onSubmit={submit} className="mx-auto flex w-full max-w-3xl items-end gap-2 px-5 py-4 sm:px-8">
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
          placeholder="Ask why, who knows, or what breaks…"
          aria-label="Question"
          className="max-h-40 min-h-[2.75rem] flex-1 resize-none rounded-xl border border-line bg-surface px-4 py-3 text-[15px] text-fg placeholder:text-faint focus:border-accent focus:outline-none"
        />
        {busy ? (
          <button type="button" onClick={onStop} className="grid h-11 w-11 place-items-center rounded-xl border border-line bg-surface text-fg hover:bg-surface-2" aria-label="Stop">
            <Square className="h-4 w-4" />
          </button>
        ) : (
          <button type="submit" disabled={!q.trim()} className="grid h-11 w-11 place-items-center rounded-xl bg-accent text-accent-fg transition disabled:opacity-40" aria-label="Ask">
            <ArrowUp className="h-5 w-5" />
          </button>
        )}
      </form>
    </div>
  )
}
