import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d'
import { linkEnds, type FGLink, type FGNode, type GraphData } from '../graph/useGraph'
import { LEGEND, nodeCaption, nodeColor, nodeRadius, nodeTitle } from '../graph/style'

interface Props {
  data: GraphData
  highlight: Set<string>
  selected: string | null
  onSelect: (id: string) => void
}

const EDGE_COLORS: Record<string, string> = {
  CHALLENGES: '#f87171',
  BECAUSE: '#a3e635',
  REJECTED: '#fb923c',
  EVIDENCED_BY: '#a3e635',
  DEPENDS_ON: '#2dd4bf',
  DECIDED: '#f5b14c',
}

export function GraphCanvas({ data, highlight, selected, onSelect }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const fg = useRef<ForceGraphMethods<FGNode, FGLink> | undefined>(undefined)
  const [size, setSize] = useState({ w: 800, h: 600 })
  const [hover, setHover] = useState<FGNode | null>(null)

  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }))
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const charge = fg.current?.d3Force('charge')
    charge?.strength?.(-90)
    const link = fg.current?.d3Force('link')
    link?.distance?.((l: FGLink) => (l.type === 'PART_OF' ? 26 : l.type === 'KNOWS_ABOUT' ? 70 : 45))
  }, [])

  // When a new reasoning path lights up, frame it. New nodes start wherever the simulation drops them, so
  // frame once early (feedback) and again when the layout settles (onEngineStop), when positions are final.
  const pendingFit = useRef(false)
  const fit = useCallback(() => fg.current?.zoomToFit(600, 70, (n: FGNode) => highlight.has(n.id)), [highlight])
  useEffect(() => {
    if (highlight.size === 0) return
    pendingFit.current = true
    const t = setTimeout(fit, 450)
    return () => clearTimeout(t)
  }, [highlight, fit])

  const hl = highlight.size > 0
  const neighbors = useMemo(() => {
    const m = new Set<string>()
    if (!hover) return m
    for (const l of data.links) {
      const [s, t] = linkEnds(l)
      if (s === hover.id) m.add(t)
      if (t === hover.id) m.add(s)
    }
    return m
  }, [hover, data.links])

  return (
    <div ref={wrap} className="relative h-full w-full overflow-hidden">
      <ForceGraph2D<FGNode, FGLink>
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        cooldownTicks={120}
        onEngineStop={() => {
          if (pendingFit.current) {
            pendingFit.current = false
            fit()
          }
        }}
        nodeId="id"
        nodeLabel={(n) => `${n.label}: ${nodeTitle(n)}`}
        onNodeClick={(n) => onSelect(n.id)}
        onNodeHover={(n) => setHover(n ?? null)}
        linkColor={(l) => {
          const [s, t] = linkEnds(l)
          const on = highlight.has(s) && highlight.has(t)
          const base = EDGE_COLORS[l.type] ?? '#64748b'
          if (hl && !on) return 'rgba(100,116,139,0.10)'
          return on ? base : `${base}66`
        }}
        linkWidth={(l) => {
          const [s, t] = linkEnds(l)
          return highlight.has(s) && highlight.has(t) ? 1.8 : 0.6
        }}
        linkDirectionalArrowLength={(l) => {
          const [s, t] = linkEnds(l)
          return highlight.has(s) && highlight.has(t) ? 3.5 : 0
        }}
        linkDirectionalArrowRelPos={0.9}
        linkDirectionalParticles={(l) => {
          const [s, t] = linkEnds(l)
          return highlight.has(s) && highlight.has(t) ? 2 : 0
        }}
        linkDirectionalParticleWidth={2}
        nodeCanvasObject={(node, ctx, scale) => {
          const r = nodeRadius(node)
          const on = !hl || highlight.has(node.id)
          const focus = node.id === selected || node.id === hover?.id
          const color = nodeColor(node)
          const x = node.x ?? 0
          const y = node.y ?? 0
          ctx.globalAlpha = on || neighbors.has(node.id) ? 1 : 0.18
          if (focus || (hl && highlight.has(node.id))) {
            ctx.beginPath()
            ctx.arc(x, y, r + 4, 0, 2 * Math.PI)
            ctx.fillStyle = `${color}33`
            ctx.fill()
          }
          ctx.beginPath()
          ctx.arc(x, y, r, 0, 2 * Math.PI)
          ctx.fillStyle = color
          ctx.fill()
          if (node.label === 'Decision' || focus) {
            ctx.lineWidth = 1.2
            ctx.strokeStyle = '#0b1020'
            ctx.stroke()
          }
          // Label every lit node only when few are lit; otherwise just the anchors (decisions, people, repos), or when zoomed in.
          const anchor = node.label === 'Decision' || node.label === 'Person' || (node.label === 'System' && node.props.kind === 'repo')
          const lit = hl && highlight.has(node.id) && (highlight.size <= 24 || anchor)
          const showLabel = focus || lit || (!hl && node.label === 'System' && node.props.kind === 'repo') || scale > 2.6
          if (showLabel) {
            const fontSize = Math.max(10 / scale, 2.4)
            ctx.font = `${focus ? 600 : 500} ${fontSize}px Inter, ui-sans-serif, system-ui`
            ctx.textAlign = 'center'
            ctx.textBaseline = 'top'
            const text = nodeCaption(node)
            const w = ctx.measureText(text).width
            ctx.fillStyle = 'rgba(11,16,32,0.75)'
            ctx.fillRect(x - w / 2 - 2, y + r + 2, w + 4, fontSize + 2)
            ctx.fillStyle = '#e2e8f0'
            ctx.fillText(text, x, y + r + 3)
          }
          ctx.globalAlpha = 1
        }}
        nodePointerAreaPaint={(node, color, ctx) => {
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.arc(node.x ?? 0, node.y ?? 0, nodeRadius(node) + 3, 0, 2 * Math.PI)
          ctx.fill()
        }}
      />
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 rounded-lg bg-ink-900/80 px-3 py-2 text-[11px] text-slate-300 backdrop-blur">
        {LEGEND.map((l) => (
          <span key={l.label} className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
      </div>
      {data.nodes.length === 0 && (
        <div className="absolute inset-0 grid place-items-center text-sm text-slate-500">Loading graph…</div>
      )}
    </div>
  )
}
