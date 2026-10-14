import { ExternalLink, Loader2, Maximize2, Quote, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { ActionsPanel } from './Actions'
import { nodeColor } from '../graph/style'
import type { Citation, GNode, Stats, SubGraph, ToolResponse } from '../types'

export type Tab = 'evidence' | 'insights' | 'actions' | 'node'

interface Props {
  tab: Tab
  setTab: (t: Tab) => void
  citations: Citation[]
  activeCite: string | null
  onCite: (c: Citation) => void
  selected: string | null
  onShowSubgraph: (sg: SubGraph) => void
}

export function SidePanel(props: Props) {
  const { tab, setTab } = props
  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-1 border-b border-ink-700 px-3 pt-2" role="tablist">
        {(['evidence', 'insights', 'actions', 'node'] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-t-md px-3 py-2 text-xs font-medium capitalize transition ${tab === t ? 'bg-ink-800 text-slate-100' : 'text-slate-500 hover:text-slate-300'}`}
          >
            {t === 'node' ? 'Details' : t}
            {t === 'evidence' && props.citations.length > 0 && <span className="ml-1.5 text-teal-300">{props.citations.length}</span>}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto bg-ink-800/40 p-3">
        {tab === 'evidence' && <Evidence {...props} />}
        {tab === 'insights' && <Insights onShowSubgraph={props.onShowSubgraph} />}
        {tab === 'actions' && <ActionsPanel />}
        {tab === 'node' && <NodeDetail id={props.selected} onShowSubgraph={props.onShowSubgraph} />}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ evidence

function Evidence({ citations, activeCite, onCite }: Props) {
  useEffect(() => {
    if (activeCite) document.getElementById(`cite-card-${activeCite}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [activeCite])
  if (citations.length === 0) {
    return <Empty>Sources cited by the latest answer appear here, each with the exact quote that supports it.</Empty>
  }
  return (
    <ul className="space-y-2">
      {citations.map((c) => (
        <li
          key={c.uid}
          id={`cite-card-${c.uid}`}
          onClick={() => onCite(c)}
          className={`cursor-pointer rounded-lg border p-3 transition ${activeCite === c.uid ? 'border-teal-400/60 bg-teal-400/5' : 'border-ink-600 bg-ink-800 hover:border-ink-600/80'}`}
        >
          <div className="flex items-start gap-2">
            <span className="mt-0.5 rounded bg-teal-400/15 px-1.5 font-mono text-[10px] font-semibold text-teal-200">{c.ref}</span>
            <div className="min-w-0 flex-1">
              <a href={c.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="group flex items-start gap-1 text-sm text-slate-100 hover:text-teal-200">
                <span className="line-clamp-2">{c.title}</span>
                <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 opacity-50 group-hover:opacity-100" />
              </a>
              <p className="mt-0.5 text-[11px] text-slate-500">
                {c.kind.toUpperCase()} · {c.uid}
                {c.date && ` · ${c.date}`}
              </p>
            </div>
          </div>
          {c.quote && (
            <blockquote className="mt-2 flex gap-1.5 border-l-2 border-lime-400/60 pl-2 text-xs italic leading-relaxed text-slate-300">
              <Quote className="h-3 w-3 shrink-0 text-lime-300/70" />
              {c.quote}
            </blockquote>
          )}
        </li>
      ))}
    </ul>
  )
}

// ------------------------------------------------------------------ insights

interface RiskData {
  at_risk_modules: { system: string; bus_factor: number; top_expert: string; top_share: number; prs: number }[]
  bridge_people: { handle: string; betweenness: number }[]
}
interface TeamsData {
  teams: { people: string[]; focus_systems: string[] }[]
}
interface DriftData {
  drifted: { decision: string; title: string; decided: string; premise: string; challenged_by: string; on: string; quote: string }[]
}

function useTool<T>(name: string) {
  const [res, setRes] = useState<ToolResponse<T> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(() => {
    setLoading(true)
    setErr(null)
    api
      .tool<T>(name)
      .then(setRes)
      .catch((e: Error) => setErr(e.message))
      .finally(() => setLoading(false))
  }, [name])
  useEffect(() => {
    load()
  }, [load])
  return { res, err, loading, load }
}

function Insights({ onShowSubgraph }: { onShowSubgraph: (sg: SubGraph) => void }) {
  const risk = useTool<RiskData>('bus_factor')
  const teams = useTool<TeamsData>('teams')
  const drift = useTool<DriftData>('drift_report')
  const [stats, setStats] = useState<Stats | null>(null)
  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null))
  }, [])

  return (
    <div className="space-y-4">
      <Section title="Decision drift" loading={drift.loading} onShow={drift.res ? () => onShowSubgraph(drift.res!.subgraph) : undefined} onRefresh={drift.load}>
        {drift.err && <ErrorText>{drift.err}</ErrorText>}
        {drift.res && drift.res.data.drifted.length === 0 && <Muted>No decision premise has been contradicted yet. Run `cairn drift` after extraction.</Muted>}
        {drift.res?.data.drifted.slice(0, 6).map((d) => (
          <div key={d.decision + d.challenged_by} className="rounded-md bg-red-500/5 p-2 ring-1 ring-red-400/20">
            <p className="text-xs font-medium text-red-200">{d.title}</p>
            <p className="mt-1 text-[11px] text-slate-400">Assumed: {d.premise}</p>
            <p className="mt-1 text-[11px] italic text-slate-300">
              “{d.quote}” <span className="not-italic text-slate-500">· {d.challenged_by}, {d.on}</span>
            </p>
          </div>
        ))}
      </Section>

      <Section title="Knowledge risk" loading={risk.loading} onShow={risk.res ? () => onShowSubgraph(risk.res!.subgraph) : undefined} onRefresh={risk.load}>
        {risk.err && <ErrorText>{risk.err}</ErrorText>}
        {risk.res?.data.at_risk_modules.slice(0, 7).map((m) => (
          <div key={m.system} className="flex items-center gap-2 text-xs">
            <div className="h-1.5 w-16 overflow-hidden rounded bg-ink-600">
              <div className="h-full bg-amber-400" style={{ width: `${Math.round(m.top_share * 100)}%` }} />
            </div>
            <span className="min-w-0 flex-1 truncate text-slate-300" title={m.system}>
              {m.system.split(':').pop()}
            </span>
            <span className="text-slate-500">@{m.top_expert}</span>
          </div>
        ))}
        {risk.res && risk.res.data.bridge_people.length > 0 && (
          <p className="pt-1 text-[11px] text-slate-500">Bridges between areas: {risk.res.data.bridge_people.map((b) => `@${b.handle}`).join(', ')}</p>
        )}
      </Section>

      <Section title="Inferred teams" loading={teams.loading} onShow={teams.res ? () => onShowSubgraph(teams.res!.subgraph) : undefined} onRefresh={teams.load}>
        {teams.err && <ErrorText>{teams.err}</ErrorText>}
        {teams.res?.data.teams.slice(0, 4).map((t, i) => (
          <div key={i} className="text-xs">
            <p className="text-slate-300">{t.people.slice(0, 6).map((p) => `@${p}`).join(', ')}{t.people.length > 6 && ` +${t.people.length - 6}`}</p>
            <p className="text-[11px] text-slate-500">focus: {t.focus_systems.slice(0, 3).map((s) => s.split(':').pop()).join(', ')}</p>
          </div>
        ))}
      </Section>

      {stats && (
        <Section title="Graph & cost">
          <div className="grid grid-cols-3 gap-2 text-center">
            {Object.entries(stats.nodes)
              .filter(([k]) => k !== 'Answer')
              .map(([k, v]) => (
                <div key={k} className="rounded-md bg-ink-800 p-2">
                  <p className="text-sm font-semibold text-slate-100">{v.toLocaleString()}</p>
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">{k}</p>
                </div>
              ))}
          </div>
          {Object.keys(stats.llm_usage).length > 0 && (
            <table className="mt-2 w-full text-[11px] text-slate-400">
              <thead>
                <tr className="text-slate-500">
                  <th className="text-left font-normal">role</th>
                  <th className="text-right font-normal">calls</th>
                  <th className="text-right font-normal">cached</th>
                  <th className="text-right font-normal">tokens</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(stats.llm_usage).map(([role, u]) => (
                  <tr key={role}>
                    <td>{role}</td>
                    <td className="text-right">{u.calls}</td>
                    <td className="text-right">{u.cache_hits}</td>
                    <td className="text-right">{(u.prompt_tokens + u.completion_tokens).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ node detail

const HIDDEN_PROPS = new Set(['body', 'labels', 'extract_version', 'drift_version'])

function NodeDetail({ id, onShowSubgraph }: { id: string | null; onShowSubgraph: (sg: SubGraph) => void }) {
  const [node, setNode] = useState<{ label: string; props: Record<string, unknown>; neighbors: SubGraph } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!id) return
    setNode(null)
    setErr(null)
    api.node(id).then(setNode).catch((e: Error) => setErr(e.message))
  }, [id])

  if (!id) return <Empty>Click any node in the graph to inspect it and expand its neighbourhood.</Empty>
  if (err) return <ErrorText>{err}</ErrorText>
  if (!node) return <Loader2 className="h-4 w-4 animate-spin text-slate-500" />

  const asNode: GNode = { id, label: node.label, props: node.props }
  const url = typeof node.props.url === 'string' ? node.props.url : null
  const rels = node.neighbors.edges.reduce<Record<string, number>>((acc, e) => ({ ...acc, [e.type]: (acc[e.type] ?? 0) + 1 }), {})
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="h-3 w-3 rounded-full" style={{ background: nodeColor(asNode) }} />
        <span className="text-[11px] uppercase tracking-wide text-slate-500">{node.label}</span>
      </div>
      <h3 className="text-sm font-semibold text-slate-100">{String(node.props.title ?? node.props.name ?? node.props.text ?? id.split(':').slice(1).join(':'))}</h3>
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-teal-300 hover:underline">
          Open on GitHub <ExternalLink className="h-3 w-3" />
        </a>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {Object.entries(node.props)
          .filter(([k, v]) => !HIDDEN_PROPS.has(k) && v != null && v !== '' && k !== 'url')
          .map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-slate-500">{k}</dt>
              <dd className="break-words text-slate-300">{formatProp(k, v)}</dd>
            </div>
          ))}
      </dl>
      <div className="flex flex-wrap gap-1">
        {Object.entries(rels).map(([t, n]) => (
          <span key={t} className="rounded bg-ink-700 px-1.5 py-0.5 text-[10px] text-slate-400">
            {t} · {n}
          </span>
        ))}
      </div>
      <button onClick={() => onShowSubgraph(node.neighbors)} className="inline-flex items-center gap-1.5 rounded-md bg-teal-400/15 px-2.5 py-1.5 text-xs text-teal-200 ring-1 ring-teal-400/30 hover:bg-teal-400/25">
        <Maximize2 className="h-3.5 w-3.5" /> Expand {node.neighbors.nodes.length} neighbours
      </button>
      {typeof node.props.body === 'string' && node.props.body && (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-ink-900 p-2 text-[11px] leading-relaxed text-slate-400">{node.props.body.slice(0, 3000)}</pre>
      )}
    </div>
  )
}

function formatProp(k: string, v: unknown): string {
  if ((k === 'ts' || k.endsWith('_ts')) && typeof v === 'number') return new Date(v).toISOString().slice(0, 10)
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3)
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}

// ------------------------------------------------------------------ bits

function Section({ title, children, loading, onShow, onRefresh }: { title: string; children: React.ReactNode; loading?: boolean; onShow?: () => void; onRefresh?: () => void }) {
  return (
    <section className="space-y-2 rounded-lg border border-ink-600 bg-ink-800 p-3">
      <header className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
        <div className="flex items-center gap-2">
          {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" />}
          {onShow && (
            <button onClick={onShow} className="text-[11px] text-teal-300 hover:underline">
              show in graph
            </button>
          )}
          {onRefresh && (
            <button onClick={onRefresh} aria-label={`Refresh ${title}`} className="text-slate-500 hover:text-slate-300">
              <RefreshCw className="h-3 w-3" />
            </button>
          )}
        </div>
      </header>
      {children}
    </section>
  )
}

const Empty = ({ children }: { children: React.ReactNode }) => <p className="p-2 text-sm leading-relaxed text-slate-500">{children}</p>
const Muted = ({ children }: { children: React.ReactNode }) => <p className="text-xs text-slate-500">{children}</p>
const ErrorText = ({ children }: { children: React.ReactNode }) => <p className="text-xs text-red-300">{children}</p>
