import { AlertTriangle, ArrowDownLeft, BookOpen, GitCompareArrows, ShieldCheck, Siren, UserRoundMinus } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { ActionCard } from '../components/ActionCard'
import { GraphView } from '../components/GraphView'
import { Markdown } from '../components/Markdown'
import { Avatar, Badge, Button, Card, ErrorNote, Input, Page, SectionTitle, Textarea } from '../components/ui'
import { api } from '../lib/api'
import { kindLabel, pct, shortSystem } from '../lib/format'
import { href, sourceHref } from '../lib/router'
import type { Theme } from '../lib/theme'
import type { Action, ChangeImpact, DecisionGuard, HandoffPlan, IncidentMemory, ToolResponse } from '../lib/types'
import { useAsync } from '../lib/useAsync'

function useRun<T>() {
  const [result, setResult] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const run = (p: Promise<T>) => {
    setBusy(true)
    setError(null)
    p.then(setResult)
      .catch(setError)
      .finally(() => setBusy(false))
  }
  return { result, error, busy, run }
}

function Propose({ label, onPropose }: { label: string; onPropose: () => Promise<Action> }) {
  const [action, setAction] = useState<Action | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  if (action) return <ActionCard action={action} onChange={setAction} defaultOpen />
  return (
    <div className="space-y-2">
      <Button
        variant="primary"
        loading={busy}
        onClick={() => {
          setBusy(true)
          setError(null)
          onPropose()
            .then(setAction)
            .catch(setError)
            .finally(() => setBusy(false))
        }}
      >
        {label}
      </Button>
      <p className="text-xs text-faint">Creates a pending action. Nothing is published until someone approves it.</p>
      <ErrorNote error={error} />
    </div>
  )
}

function Shell({ icon, title, subtitle, form, children }: { icon: ReactNode; title: string; subtitle: string; form: ReactNode; children: ReactNode }) {
  return (
    <Page title={<span className="flex items-center gap-2.5">{icon}{title}</span>} subtitle={subtitle}>
      <Card className="p-4">{form}</Card>
      <div className="mt-6">{children}</div>
    </Page>
  )
}

// --------------------------------------------------------------------------- change impact

export function ChangePage({ initial, theme }: { initial: string | null; theme: Theme }) {
  const systems = useAsync(() => api.systems(), [])
  const [system, setSystem] = useState(initial ?? '')
  const [change, setChange] = useState('')
  const { result, error, busy, run } = useRun<ToolResponse<ChangeImpact>>()
  const options = systems.data?.items.flatMap((s) => [s.key, ...s.modules.map((m) => m.key)]) ?? []
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (system.trim()) run(api.changeImpact(system.trim(), change.trim()))
  }
  const d = result?.data
  return (
    <Shell
      icon={<GitCompareArrows className="h-5 w-5 text-accent" />}
      title="Change impact"
      subtitle="Before you change a service, table or API: what depends on it, which decisions it touches, and who needs to know."
      form={
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-muted">System</span>
            <Input list="systems-list" value={system} onChange={(e) => setSystem(e.target.value)} placeholder="e.g. events-pipeline" aria-label="System" />
            <datalist id="systems-list">
              {options.map((o) => (
                <option key={o} value={o} />
              ))}
            </datalist>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-muted">What's changing (optional)</span>
            <Input value={change} onChange={(e) => setChange(e.target.value)} placeholder="e.g. rename the order.created topic" aria-label="Change" />
          </label>
          <Button variant="primary" type="submit" loading={busy} disabled={!system.trim()}>
            Assess
          </Button>
        </form>
      }
    >
      <ErrorNote error={error} />
      {d && result && (
        <div className="fade-up">
          <p className="text-sm text-muted">{result.summary}</p>
          <SectionTitle>Affected downstream</SectionTitle>
          {d.dependents.length === 0 ? (
            <p className="text-sm text-muted">Nothing in the graph depends on it.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {d.dependents.map((x) => (
                <a key={x.repo} href={href('systems', x.repo)} className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-sm hover:border-line-strong">
                  <ArrowDownLeft className="h-3.5 w-3.5 text-faint" /> {shortSystem(x.repo)}
                  {x.hops > 1 && <span className="text-xs text-faint">· {x.hops} hops</span>}
                </a>
              ))}
            </div>
          )}
          {d.constraints.length > 0 && (
            <>
              <SectionTitle>Decisions to respect</SectionTitle>
              <div className="space-y-2">
                {d.constraints.map((c) => (
                  <Card key={c.uid} className="p-3.5">
                    <a href={href('decisions', c.uid)} className="text-sm font-medium text-fg hover:underline">
                      {c.title}
                    </a>
                    {c.drift_detected && (
                      <Badge tone="danger" className="ml-2">
                        <AlertTriangle className="h-3 w-3" /> Reasons challenged
                      </Badge>
                    )}
                    <ul className="mt-1.5 list-disc pl-4 text-[13px] text-muted">
                      {c.reasons.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  </Card>
                ))}
              </div>
            </>
          )}
          <SectionTitle>Who to tell</SectionTitle>
          <Card className="divide-y divide-line">
            {d.notify.map((n) => (
              <div key={n.system} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
                <span className="min-w-0 flex-1 truncate text-fg">{shortSystem(n.system)}</span>
                <Badge>{n.why}</Badge>
                {n.team && <Badge tone="accent">{n.team}</Badge>}
                <span className="flex gap-1">
                  {n.people.map((p) => (
                    <a key={p} href={href('people', p)} title={p}>
                      <Avatar handle={p} size={22} />
                    </a>
                  ))}
                </span>
              </div>
            ))}
          </Card>
          <SectionTitle>Draft heads-up</SectionTitle>
          <Card className="p-4">
            <Markdown text={d.draft} className="text-[14px]" />
          </Card>
          <div className="mt-4">
            <Propose label="Send heads-up for approval" onPropose={() => api.proposeChange(system.trim(), change.trim())} />
          </div>
          <SectionTitle>Graph</SectionTitle>
          <GraphView graph={result.subgraph} theme={theme} height={340} />
        </div>
      )}
    </Shell>
  )
}

// --------------------------------------------------------------------------- incident memory

export function IncidentPage({ theme }: { theme: Theme }) {
  const [text, setText] = useState('')
  const { result, error, busy, run } = useRun<ToolResponse<IncidentMemory>>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (text.trim().length >= 3) run(api.incident(text.trim()))
  }
  const d = result?.data
  return (
    <Shell
      icon={<Siren className="h-5 w-5 text-accent" />}
      title="Incident memory"
      subtitle="Describe what's broken. Cairn finds similar past incidents, what fixed them, who handled them, and runbooks that may be out of date."
      form={
        <form onSubmit={submit} className="space-y-3">
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. consumer lag growing on the event bus, payments events delayed" aria-label="Symptoms" />
          <Button variant="primary" type="submit" loading={busy} disabled={text.trim().length < 3}>
            Search memory
          </Button>
        </form>
      }
    >
      <ErrorNote error={error} />
      {d && result && (
        <div className="fade-up">
          {d.similar_incidents.length === 0 ? (
            <p className="text-sm text-muted">{d.note ?? 'No similar past incident found.'}</p>
          ) : (
            <>
              <SectionTitle>Similar past incidents</SectionTitle>
              <div className="space-y-2">
                {d.similar_incidents.map((i) => {
                  const link = sourceHref(i.uid, result.evidence.find((e) => e.uid === i.uid)?.url)
                  return (
                    <Card key={i.uid} className="p-4">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
                        <Badge>{kindLabel(i.kind)}</Badge>
                        {i.date}
                      </div>
                      <a href={link.href} target={link.external ? '_blank' : undefined} rel="noreferrer" className="mt-1.5 block text-sm font-medium text-fg hover:underline">
                        {i.title}
                      </a>
                      {i.what_fixed_it && (
                        <p className="mt-2 rounded-lg bg-ok-soft px-3 py-1.5 text-[13px] text-fg">
                          <span className="font-medium text-ok">What fixed it: </span>
                          {i.what_fixed_it}
                        </p>
                      )}
                      {i.handled_by.length > 0 && (
                        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                          Handled by
                          {i.handled_by.map((h) => (
                            <a key={h.handle} href={href('people', h.handle)} className={h.left_company ? 'line-through' : 'text-fg hover:underline'} title={h.left_company ? `left ${h.left_company}` : undefined}>
                              {h.handle}
                            </a>
                          ))}
                        </p>
                      )}
                    </Card>
                  )
                })}
              </div>
            </>
          )}
          {d.runbooks.length > 0 && (
            <>
              <SectionTitle>Runbooks</SectionTitle>
              <div className="space-y-2">
                {d.runbooks.map((rb) => (
                  <Card key={rb.uid} className="p-4">
                    <a href={href('source', rb.uid)} className="flex items-center gap-2 text-sm font-medium text-fg hover:underline">
                      <BookOpen className="h-4 w-4 text-faint" /> {rb.title}
                    </a>
                    {rb.possibly_outdated && (
                      <div className="mt-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px]">
                        <p className="flex items-center gap-1.5 font-medium text-warn">
                          <AlertTriangle className="h-3.5 w-3.5" /> May be out of date
                        </p>
                        <ul className="mt-1 list-disc pl-4 text-muted">
                          {rb.why.map((w) => (
                            <li key={w}>{w}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </Card>
                ))}
              </div>
            </>
          )}
          {d.who_to_page.length > 0 && (
            <>
              <SectionTitle>Who to page now</SectionTitle>
              <div className="flex flex-wrap gap-2">
                {d.who_to_page.map((p) => (
                  <a key={p.handle} href={href('people', p.handle)} className="inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-1 pr-3 text-sm hover:border-line-strong">
                    <Avatar handle={p.handle} size={22} /> {p.handle}
                    <span className="text-xs text-faint">{shortSystem(p.system)}</span>
                  </a>
                ))}
              </div>
            </>
          )}
          <SectionTitle>Graph</SectionTitle>
          <GraphView graph={result.subgraph} theme={theme} height={340} />
        </div>
      )}
    </Shell>
  )
}

// --------------------------------------------------------------------------- handoff

export function HandoffPage({ initial, theme }: { initial: string | null; theme: Theme }) {
  const people = useAsync(() => api.people(), [])
  const [handle, setHandle] = useState(initial ?? '')
  const { result, error, busy, run } = useRun<ToolResponse<HandoffPlan>>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (handle.trim()) run(api.handoff(handle.trim()))
  }
  const d = result?.data
  return (
    <Shell
      icon={<UserRoundMinus className="h-5 w-5 text-accent" />}
      title="Handoff"
      subtitle="Before someone leaves or goes on leave: what only they know, who should take it over, and what to read first."
      form={
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <label className="min-w-60 flex-1 text-sm">
            <span className="mb-1 block text-xs font-medium text-muted">Person</span>
            <Input list="people-list" value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="handle" aria-label="Person" />
            <datalist id="people-list">
              {people.data?.items.map((p) => (
                <option key={p.handle} value={p.handle}>
                  {p.name ?? ''}
                </option>
              ))}
            </datalist>
          </label>
          <Button variant="primary" type="submit" loading={busy} disabled={!handle.trim()}>
            Build plan
          </Button>
        </form>
      }
    >
      <ErrorNote error={error} />
      {d && result && (
        <div className="fade-up">
          {d.person.left_company && <p className="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">Left the company on {d.person.left_company}.</p>}
          <SectionTitle>Areas to hand over</SectionTitle>
          {d.areas.length === 0 ? (
            <p className="text-sm text-muted">No area depends heavily on this person.</p>
          ) : (
            <div className="space-y-2">
              {d.areas.map((a) => (
                <Card key={a.system} className="p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={href('systems', a.system)} className="text-sm font-medium text-fg hover:underline">
                      {shortSystem(a.system)}
                    </a>
                    <Badge tone={a.share_of_knowledge >= 0.5 ? 'warn' : 'neutral'}>{pct(a.share_of_knowledge)} of recent activity</Badge>
                  </div>
                  <p className="mt-2 text-sm text-muted">
                    Successor:{' '}
                    {a.successor ? (
                      <a href={href('people', a.successor)} className="text-fg hover:underline">
                        {a.successor}
                      </a>
                    ) : (
                      'nobody yet'
                    )}
                    {a.backup && (
                      <>
                        {' '}
                        · backup{' '}
                        <a href={href('people', a.backup)} className="text-fg hover:underline">
                          {a.backup}
                        </a>
                      </>
                    )}
                  </p>
                  {a.read_first.length > 0 && (
                    <ul className="mt-2 space-y-1 text-[13px]">
                      {a.read_first.map((r) => {
                        const link = sourceHref(r.uid, result.evidence.find((e) => e.uid === r.uid)?.url)
                        return (
                          <li key={r.uid} className="flex items-center gap-2">
                            <Badge>{kindLabel(r.kind)}</Badge>
                            <a href={link.href} target={link.external ? '_blank' : undefined} rel="noreferrer" className="truncate text-fg hover:underline">
                              {r.title}
                            </a>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </Card>
              ))}
            </div>
          )}
          <SectionTitle>Draft handoff document</SectionTitle>
          <Card className="p-4">
            <Markdown text={d.draft} className="text-[14px]" />
          </Card>
          <div className="mt-4">
            <Propose label="Publish handoff for approval" onPropose={() => api.proposeHandoff(d.person.handle)} />
          </div>
          <SectionTitle>Graph</SectionTitle>
          <GraphView graph={result.subgraph} theme={theme} height={340} focus={`Person:${d.person.handle}`} />
        </div>
      )}
    </Shell>
  )
}

// --------------------------------------------------------------------------- decision guard

const GUARD_EXAMPLE = {
  title: 'Replace Kafka with a cron job for the events pipeline',
  body: 'Drop the MSK consumers and run a nightly batch instead.',
}

const VERDICT = {
  review: { label: 'Needs review', tone: 'danger' as const, text: 'This change may reverse a decision the team made on purpose.' },
  heads_up: { label: 'Heads up', tone: 'warn' as const, text: 'This change touches decisions the team has made. Worth a look.' },
  clear: { label: 'Clear', tone: 'ok' as const, text: 'No active decision conflicts with this change.' },
}

const LEVEL = {
  conflict: { label: 'May reverse', tone: 'danger' as const },
  challenged: { label: 'Reasons challenged', tone: 'warn' as const },
  respect: { label: 'Constrains this change', tone: 'accent' as const },
}

export function GuardPage({ theme }: { theme: Theme }) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [files, setFiles] = useState('')
  const { result, error, busy, run } = useRun<ToolResponse<DecisionGuard>>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (title.trim().length < 3) return
    const paths = files
      .split('\n')
      .map((f) => f.trim())
      .filter(Boolean)
    run(api.decisionGuard(title.trim(), body.trim(), paths))
  }
  const d = result?.data
  const verdict = d ? VERDICT[d.verdict] : null
  return (
    <Shell
      icon={<ShieldCheck className="h-5 w-5 text-accent" />}
      title="Decision guard"
      subtitle="Paste a new PR or ticket. Cairn checks it against the decisions already made on the systems it touches and warns before one is undone by accident."
      form={
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium text-muted">Title</span>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Replace Kafka with a cron job for the events pipeline" aria-label="Title" />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium text-muted">Description (optional)</span>
            <Textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder="What does this change do, and why?" aria-label="Description" />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium text-muted">Files touched, one per line (optional)</span>
            <Textarea rows={2} value={files} onChange={(e) => setFiles(e.target.value)} placeholder="src/events/consumer.py" aria-label="Files touched" />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" type="submit" loading={busy} disabled={title.trim().length < 3}>
              Check against decisions
            </Button>
            <Button
              type="button"
              onClick={() => {
                setTitle(GUARD_EXAMPLE.title)
                setBody(GUARD_EXAMPLE.body)
                setFiles('')
              }}
            >
              Try an example
            </Button>
          </div>
        </form>
      }
    >
      <ErrorNote error={error} />
      {d && result && verdict && (
        <div className="fade-up">
          <Card className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={verdict.tone}>
                {d.verdict === 'clear' ? <ShieldCheck className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />} {verdict.label}
              </Badge>
              <span className="text-sm text-fg">{verdict.text}</span>
            </div>
            {d.systems.length > 0 && (
              <p className="mt-2 text-xs text-faint">
                Systems matched: {d.systems.map((s) => shortSystem(s)).join(', ')}
              </p>
            )}
          </Card>
          {d.findings.length > 0 && (
            <>
              <SectionTitle>Decisions involved</SectionTitle>
              <div className="space-y-2">
                {d.findings.map((f) => {
                  const level = LEVEL[f.level]
                  return (
                    <Card key={f.uid} className="p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={level.tone}>{level.label}</Badge>
                        <a href={href('decisions', f.uid)} className="text-sm font-medium text-fg hover:underline">
                          {f.title}
                        </a>
                        {f.date && <span className="text-xs text-faint">{f.date}</span>}
                      </div>
                      <ul className="mt-2 list-disc pl-4 text-[13px] text-muted">
                        {f.reasons.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-faint">
                        <span>Decided by</span>
                        <span className="flex gap-1">
                          {f.deciders.map((p) => (
                            <a key={p} href={href('people', p)} title={p}>
                              <Avatar handle={p} size={22} />
                            </a>
                          ))}
                        </span>
                      </div>
                    </Card>
                  )
                })}
              </div>
            </>
          )}
          <SectionTitle>Draft comment</SectionTitle>
          <Card className="p-4">
            <Markdown text={d.draft} className="text-[14px]" />
          </Card>
          <p className="mt-2 text-xs text-faint">A draft only. Nothing is posted anywhere, and usernames are never @-mentioned.</p>
          {result.subgraph.nodes.length > 0 && (
            <>
              <SectionTitle>Graph</SectionTitle>
              <GraphView graph={result.subgraph} theme={theme} height={340} />
            </>
          )}
        </div>
      )}
    </Shell>
  )
}
