import { Database, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ask } from './api'
import { Chat } from './components/Chat'
import { ErrorBoundary } from './components/ErrorBoundary'
import { GraphCanvas } from './components/GraphCanvas'
import { SidePanel, type Tab } from './components/SidePanel'
import { useGraph } from './graph/useGraph'
import type { AgentEvent, Citation, Health, Step, SubGraph, Turn } from './types'

let turnSeq = 0

export default function App() {
  const graph = useGraph()
  const [health, setHealth] = useState<Health | null>(null)
  const [healthErr, setHealthErr] = useState<string | null>(null)
  const [overview, setOverview] = useState<SubGraph | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<Tab>('evidence')
  const [selected, setSelected] = useState<string | null>(null)
  const [citations, setCitations] = useState<Citation[]>([])
  const [activeCite, setActiveCite] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  const { merge, reset, setHighlight } = graph

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setHealthErr(e.message))
    api
      .overview()
      .then((sg) => {
        setOverview(sg)
        reset(sg)
      })
      .catch((e: Error) => setHealthErr(e.message))
  }, [reset])

  const update = useCallback((id: string, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t))), [])

  const onAsk = useCallback(
    async (question: string) => {
      abort.current?.abort()
      const controller = new AbortController()
      abort.current = controller
      const id = `t${++turnSeq}`
      const history = turns
        .filter((t) => t.answer)
        .slice(-3)
        .flatMap((t) => [
          { role: 'user', content: t.question },
          { role: 'assistant', content: t.answer!.text },
        ])
      setTurns((ts) => [...ts, { id, question, steps: [], notices: [], pending: true }])
      setBusy(true)
      setActiveCite(null)
      const lit = new Set<string>()

      const onEvent = (e: AgentEvent) => {
        switch (e.type) {
          case 'tool_call':
            update(id, (t) => ({ ...t, steps: [...t.steps, { id: e.id, name: e.name, args: e.args, nodeIds: [] }] }))
            break
          case 'tool_result': {
            merge(e.subgraph)
            const ids = e.subgraph.nodes.map((n) => n.id)
            ids.forEach((n) => lit.add(n))
            setHighlight(new Set(lit))
            update(id, (t) => ({
              ...t,
              steps: t.steps.map((s) => (s.id === e.id ? { ...s, summary: e.summary, error: e.error, nodeIds: ids } : s)),
            }))
            break
          }
          case 'notice':
            update(id, (t) => ({ ...t, notices: [...t.notices, e.message] }))
            break
          case 'answer':
            update(id, (t) => ({ ...t, answer: { text: e.text, citations: e.citations, mode: e.mode } }))
            setCitations(e.citations)
            setTab('evidence')
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
    [turns, merge, setHighlight, update],
  )

  const onCite = useCallback(
    (turn: Turn, c: Citation) => {
      if (turn.answer) setCitations(turn.answer.citations)
      setActiveCite(c.uid)
      setSelected(`Source:${c.uid}`)
      setTab('evidence')
    },
    [],
  )

  const onStep = useCallback(
    (s: Step) => {
      if (s.nodeIds.length) setHighlight(new Set(s.nodeIds))
    },
    [setHighlight],
  )

  const onShowSubgraph = useCallback(
    (sg: SubGraph) => {
      merge(sg)
      setHighlight(new Set(sg.nodes.map((n) => n.id)))
    },
    [merge, setHighlight],
  )

  const resetView = () => {
    reset(overview ?? undefined)
    setSelected(null)
  }

  const agentReady = health?.llm.agent?.ready

  return (
    <div className="flex h-dvh flex-col bg-ink-950 text-slate-200">
      <header className="flex min-w-0 items-center gap-3 border-b border-ink-700 px-4 py-2.5">
        <img src="/cairn.svg" alt="" className="h-7 w-7" />
        <div className="min-w-0">
          <h1 className="text-sm font-semibold leading-tight text-slate-100">Cairn</h1>
          <p className="truncate text-[11px] text-slate-500">The company brain that remembers why · on FalkorDB</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-[11px]">
          {healthErr && <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-red-300">API unreachable</span>}
          {health && (
            <>
              <span className="hidden rounded-full bg-ink-800 px-2 py-0.5 text-slate-400 sm:inline">
                {health.repos.length} repos · graph <span className="text-slate-200">{health.graph}</span>
              </span>
              <span
                className={`whitespace-nowrap rounded-full px-2 py-0.5 ${agentReady ? 'bg-emerald-400/15 text-emerald-300' : 'bg-amber-400/15 text-amber-300'}`}
                title={agentReady ? health.llm.agent.model : 'No agent LLM key: answers come from deterministic graph routing'}
              >
                {agentReady ? `agent · ${health.llm.agent.model.split(':')[1]}` : 'graph-only mode'}
              </span>
            </>
          )}
          <button onClick={resetView} className="flex items-center gap-1 rounded-md px-2 py-1 text-slate-400 hover:bg-ink-800 hover:text-slate-200" title="Reset graph view">
            <RotateCcw className="h-3.5 w-3.5" /> <span className="hidden md:inline">Reset view</span>
          </button>
          <a href="https://github.com/FalkorDB" target="_blank" rel="noreferrer" className="text-slate-500 hover:text-slate-300" aria-label="FalkorDB on GitHub">
            <Database className="h-4 w-4" />
          </a>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[minmax(320px,45dvh)_minmax(0,1fr)] lg:grid-cols-[380px_minmax(0,1fr)_360px] lg:grid-rows-1">
        <section className="order-2 min-h-0 border-ink-700 lg:order-1 lg:border-r" aria-label="Chat">
          <ErrorBoundary name="chat">
            <Chat turns={turns} busy={busy} onAsk={onAsk} onStop={() => abort.current?.abort()} onCite={onCite} onStep={onStep} />
          </ErrorBoundary>
        </section>
        <section className="order-1 min-h-0 bg-[radial-gradient(ellipse_at_center,_#121a30_0%,_#070b16_70%)] lg:order-2" aria-label="Knowledge graph">
          <ErrorBoundary name="graph">
          <GraphCanvas
            data={graph.data}
            highlight={graph.highlight}
            selected={selected}
            onSelect={(id) => {
              setSelected(id)
              setTab('node')
            }}
          />
          </ErrorBoundary>
        </section>
        <section className="order-3 hidden min-h-0 border-l border-ink-700 lg:block" aria-label="Details">
          <ErrorBoundary name="details">
          <SidePanel
            tab={tab}
            setTab={setTab}
            citations={citations}
            activeCite={activeCite}
            onCite={(c) => {
              setActiveCite(c.uid)
              setSelected(`Source:${c.uid}`)
            }}
            selected={selected}
            onShowSubgraph={onShowSubgraph}
          />
          </ErrorBoundary>
        </section>
      </main>
    </div>
  )
}
