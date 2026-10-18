import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d'
import { go } from '../lib/router'
import { cssVar, type Theme } from '../lib/theme'
import type { GNode, SubGraph } from '../lib/types'

interface FGNode extends GNode {
  x?: number
  y?: number
}
interface FGLink {
  id: string
  type: string
  source: string | FGNode
  target: string | FGNode
}

const ends = (l: FGLink): [string, string] => [typeof l.source === 'string' ? l.source : l.source.id, typeof l.target === 'string' ? l.target : l.target.id]

function colorVar(n: GNode): string {
  const p = n.props
  if (n.label === 'Person') return '--graph-person'
  if (n.label === 'Team') return '--graph-team'
  if (n.label === 'System') return p.kind === 'repo' ? '--graph-system' : '--graph-module'
  if (n.label === 'Decision') return p.drift ? '--graph-drift' : '--graph-decision'
  if (n.label === 'Premise') return '--graph-premise'
  if (n.label === 'Source') return p.kind === 'issue' || p.kind === 'ticket' ? '--graph-issue' : '--graph-source'
  return '--graph-edge'
}

function radius(n: GNode): number {
  if (n.label === 'System') return n.props.kind === 'repo' ? 8 : 5.5
  if (n.label === 'Decision') return 7
  if (n.label === 'Person' || n.label === 'Team') return 6
  if (n.label === 'Premise') return 4
  return 4
}

function caption(n: GNode): string {
  const p = n.props
  const cut = (s: string, k: number) => (s.length > k ? `${s.slice(0, k - 1)}…` : s)
  switch (n.label) {
    case 'Person':
      return String(p.handle ?? '')
    case 'Team':
      return String(p.name ?? p.key ?? '')
    case 'System': {
      const key = String(p.key ?? '')
      return key.includes(':') ? (key.split(':').pop() ?? key) : (key.split('/').pop() ?? key)
    }
    case 'Source': {
      const uid = String(p.uid ?? '')
      const m = uid.match(/#(\d+)(?:\/|$)/)
      if (m) return `#${m[1]}`
      if (uid.startsWith('ticket:')) return uid.slice(7).split('/')[0]
      if (uid.startsWith('doc:')) return cut(String(p.title ?? uid), 28)
      return cut(String(p.title ?? uid).replace(/^#[\w-]+: /, ''), 26)
    }
    case 'Decision':
      return cut(String(p.title ?? 'decision'), 32)
    case 'Premise':
      return cut(String(p.text ?? ''), 28)
    default:
      return n.id
  }
}

/** Open the page for a node. Premises open their decision. */
export function openNode(id: string) {
  const [label, ...rest] = id.split(':')
  const key = rest.join(':')
  if (label === 'Person') go('people', key)
  else if (label === 'System') go('systems', key)
  else if (label === 'Decision') go('decisions', key)
  else if (label === 'Source') go('source', key)
  else if (label === 'Premise') go('decisions', key.replace(/::[pr]\d+$/, '').replace(/::m\d+::[pr]\d+$/, ''))
}

interface Props {
  graph: SubGraph
  theme: Theme
  height?: number
  highlight?: Set<string>
  focus?: string | null
}

export function GraphView({ graph, theme, height = 420, highlight, focus }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const fg = useRef<ForceGraphMethods<FGNode, FGLink> | undefined>(undefined)
  const [width, setWidth] = useState(600)
  const [hover, setHover] = useState<string | null>(null)
  // Keep node objects stable across renders so the layout doesn't restart.
  const cache = useRef(new Map<string, FGNode>())
  const data = useMemo(() => {
    const nodes = graph.nodes.map((n) => {
      const prev = cache.current.get(n.id)
      if (prev) {
        prev.props = { ...prev.props, ...n.props }
        return prev
      }
      const node: FGNode = { ...n, props: { ...n.props } }
      cache.current.set(n.id, node)
      return node
    })
    const ids = new Set(nodes.map((n) => n.id))
    const links = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ id: e.id, type: e.type, source: e.source, target: e.target }))
    return { nodes, links }
  }, [graph])

  // Colours come from CSS variables so the canvas follows the theme.
  const palette = useMemo(() => {
    const keys = ['--graph-person', '--graph-team', '--graph-system', '--graph-module', '--graph-decision', '--graph-drift', '--graph-premise', '--graph-source', '--graph-issue', '--graph-edge', '--graph-label-bg', '--graph-label', '--accent']
    return Object.fromEntries(keys.map((k) => [k, cssVar(k)]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])

  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    fg.current?.d3Force('charge')?.strength?.(-110)
    fg.current?.d3Force('link')?.distance?.(48)
  }, [])

  const fit = useCallback(() => fg.current?.zoomToFit(500, 40, (n: FGNode) => !highlight || highlight.size === 0 || highlight.has(n.id)), [highlight])
  const pending = useRef(true)
  useEffect(() => {
    pending.current = true
    const t = setTimeout(fit, 300)
    return () => clearTimeout(t)
  }, [graph, fit])

  const neighbours = useMemo(() => {
    const s = new Set<string>()
    if (!hover) return s
    for (const l of data.links) {
      const [a, b] = ends(l)
      if (a === hover) s.add(b)
      if (b === hover) s.add(a)
    }
    return s
  }, [hover, data.links])

  const focusing = !!highlight && highlight.size > 0
  const lit = (id: string) => !highlight || highlight.size === 0 || highlight.has(id)

  return (
    <div ref={wrap} className="relative w-full overflow-hidden rounded-xl border border-line bg-surface" style={{ height }}>
      {data.nodes.length === 0 ? (
        <div className="flex h-full items-center justify-center text-sm text-faint">Nothing to draw yet.</div>
      ) : (
        <ForceGraph2D<FGNode, FGLink>
          ref={fg}
          width={width}
          height={height}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={110}
          onEngineStop={() => {
            if (pending.current) {
              pending.current = false
              fit()
            }
          }}
          nodeLabel={(n) => `${n.label}: ${caption(n)}`}
          onNodeClick={(n) => openNode(n.id)}
          onNodeHover={(n) => setHover(n?.id ?? null)}
          linkColor={(l) => {
            const [a, b] = ends(l)
            const on = lit(a) && lit(b)
            if (l.type === 'CHALLENGES') return palette['--graph-drift']
            if (focusing && on) return palette['--accent']
            return on ? palette['--graph-edge'] : `${palette['--graph-edge']}55`
          }}
          linkWidth={(l) => {
            const [a, b] = ends(l)
            if (l.type === 'CHALLENGES') return 1.6
            return focusing && lit(a) && lit(b) ? 1.8 : 1
          }}
          linkDirectionalParticles={(l) => {
            const [a, b] = ends(l)
            return focusing && lit(a) && lit(b) ? 3 : 0
          }}
          linkDirectionalParticleWidth={2.4}
          linkDirectionalParticleSpeed={0.006}
          linkDirectionalParticleColor={() => palette['--accent']}
          linkDirectionalArrowLength={3}
          linkDirectionalArrowRelPos={0.92}
          nodeCanvasObject={(node, ctx, scale) => {
            const r = radius(node)
            const x = node.x ?? 0
            const y = node.y ?? 0
            const isFocus = node.id === focus || node.id === hover
            const dim = !lit(node.id) && !neighbours.has(node.id) && !isFocus
            ctx.globalAlpha = dim ? 0.25 : 1
            ctx.beginPath()
            ctx.arc(x, y, r, 0, 2 * Math.PI)
            ctx.fillStyle = palette[colorVar(node)]
            ctx.fill()
            if (isFocus) {
              ctx.lineWidth = 2 / scale
              ctx.strokeStyle = palette['--accent']
              ctx.stroke()
            }
            const showLabel = isFocus || node.label !== 'Premise' || scale > 1.8
            if (showLabel && !dim) {
              const size = Math.max(10 / scale, 2.2)
              ctx.font = `500 ${size}px Inter, ui-sans-serif, system-ui`
              ctx.textAlign = 'center'
              ctx.textBaseline = 'top'
              const text = caption(node)
              const w = ctx.measureText(text).width
              ctx.fillStyle = palette['--graph-label-bg']
              ctx.fillRect(x - w / 2 - 2, y + r + 2, w + 4, size + 2)
              ctx.fillStyle = palette['--graph-label']
              ctx.fillText(text, x, y + r + 3)
            }
            ctx.globalAlpha = 1
          }}
          nodePointerAreaPaint={(node, color, ctx) => {
            ctx.fillStyle = color
            ctx.beginPath()
            ctx.arc(node.x ?? 0, node.y ?? 0, radius(node) + 4, 0, 2 * Math.PI)
            ctx.fill()
          }}
        />
      )}
      <Legend />
    </div>
  )
}

function Legend() {
  const items: [string, string][] = [
    ['Person', '--graph-person'],
    ['System', '--graph-system'],
    ['Decision', '--graph-decision'],
    ['Reason', '--graph-premise'],
    ['Challenged', '--graph-drift'],
    ['Source', '--graph-source'],
    ['Ticket/issue', '--graph-issue'],
  ]
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 flex flex-wrap gap-x-3 gap-y-1 rounded-md bg-surface/80 px-2 py-1 text-[10.5px] text-muted backdrop-blur">
      {items.map(([label, v]) => (
        <span key={label} className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full" style={{ background: `var(${v})` }} />
          {label}
        </span>
      ))}
    </div>
  )
}
