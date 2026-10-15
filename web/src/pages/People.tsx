import { ArrowLeft, Search, UserRoundMinus, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { GraphView } from '../components/GraphView'
import { Avatar, Badge, Button, Card, Empty, ErrorNote, Input, Meter, Page, SectionTitle, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { kindLabel, pct, shortSystem } from '../lib/format'
import { go, href, sourceHref } from '../lib/router'
import type { Theme } from '../lib/theme'
import { useAsync } from '../lib/useAsync'

export function PeoplePage({ id, theme }: { id: string | null; theme: Theme }) {
  return id ? <PersonView handle={id} theme={theme} /> : <PeopleList />
}

function PeopleList() {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 200)
    return () => clearTimeout(t)
  }, [query])
  const { data, error, loading } = useAsync(() => api.people(debounced), [debounced])
  return (
    <Page title="People" subtitle="Who knows what, ranked by recent work: code, reviews, tickets, docs and decisions.">
      <div className="relative mb-5">
        <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-faint" />
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search people" className="pl-9" aria-label="Search people" />
      </div>
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : data && data.items.length === 0 ? (
        <Empty title="No people found" icon={<Users className="h-6 w-6" />} />
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {data?.items.map((p) => (
            <a key={p.handle} href={href('people', p.handle)} className="flex gap-3 rounded-xl border border-line bg-surface p-3.5 transition hover:border-line-strong">
              <Avatar handle={p.handle} size={36} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-fg">{p.name ?? p.handle}</p>
                <p className="truncate text-xs text-faint">
                  {p.handle}
                  {p.decisions > 0 && ` · ${p.decisions} decisions`}
                </p>
                <div className="mt-2">
                  <Meter value={p.activity} />
                </div>
                {p.areas.length > 0 && <p className="mt-2 truncate text-xs text-muted">{p.areas.map(shortSystem).join(' · ')}</p>}
              </div>
            </a>
          ))}
        </div>
      )}
    </Page>
  )
}

function PersonView({ handle, theme }: { handle: string; theme: Theme }) {
  const { data, error, loading } = useAsync(() => api.person(handle), [handle])
  if (loading && !data) return <Page title={handle}><Spinner /></Page>
  if (error || !data) return <Page title={handle}><ErrorNote error={error ?? 'Not found'} /></Page>
  const { person, expertise, decisions, open_work, would_leave_gap_in } = data.person
  return (
    <Page
      title={
        <span className="flex items-center gap-3">
          <Avatar handle={person.handle} size={40} departed={!!person.left_company} />
          <span>
            {person.name ?? person.handle}
            <span className="block text-sm font-normal text-muted">
              {[person.role, person.team, person.handle].filter(Boolean).join(' · ')}
            </span>
          </span>
        </span>
      }
      actions={
        <>
          <a href={href('people')} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
            <ArrowLeft className="h-4 w-4" /> People
          </a>
          <Button icon={<UserRoundMinus className="h-4 w-4" />} onClick={() => go('handoff', person.handle)}>
            Handoff plan
          </Button>
        </>
      }
    >
      {person.left_company && (
        <p className="mb-4 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
          Left the company on {person.left_company}. Their knowledge below may no longer be reachable.
        </p>
      )}
      <SectionTitle>Knows about</SectionTitle>
      {expertise.length === 0 ? (
        <p className="text-sm text-muted">No recorded activity.</p>
      ) : (
        <Card className="divide-y divide-line">
          {expertise.map((e) => (
            <a key={e.system} href={href('systems', e.system)} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
              <span className="w-40 shrink-0 truncate text-sm text-fg sm:w-56">{shortSystem(e.system)}</span>
              <div className="flex-1">
                <Meter value={e.share_of_knowledge} tone={e.share_of_knowledge >= 0.5 ? 'warn' : 'accent'} />
              </div>
              <span className="w-12 text-right text-xs text-muted">{pct(e.share_of_knowledge)}</span>
              {would_leave_gap_in.includes(e.system) && <Badge tone="warn">Key holder</Badge>}
            </a>
          ))}
        </Card>
      )}
      {decisions.length > 0 && (
        <>
          <SectionTitle>Decisions</SectionTitle>
          <ul className="space-y-1.5">
            {decisions.map((d) => (
              <li key={d.uid} className="text-sm">
                <a href={href('decisions', d.uid)} className="text-fg hover:underline">
                  {d.title}
                </a>{' '}
                <span className="text-xs text-faint">{d.date}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {open_work.length > 0 && (
        <>
          <SectionTitle>{person.left_company ? 'Open work left behind' : 'Open work'}</SectionTitle>
          <ul className="space-y-1.5">
            {open_work.map((w) => {
              const link = sourceHref(w.uid, data.sources.find((s) => s.uid === w.uid)?.url)
              return (
                <li key={w.uid} className="flex items-center gap-2 text-sm">
                  <Badge>{kindLabel(w.kind)}</Badge>
                  <a href={link.href} target={link.external ? '_blank' : undefined} rel="noreferrer" className="truncate text-fg hover:underline">
                    {w.title}
                  </a>
                </li>
              )
            })}
          </ul>
        </>
      )}
      <SectionTitle>Graph</SectionTitle>
      <GraphView graph={data.graph} theme={theme} height={340} focus={`Person:${person.handle}`} />
    </Page>
  )
}
