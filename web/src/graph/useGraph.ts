import { useCallback, useRef, useState } from 'react'
import type { GEdge, GNode, SubGraph } from '../types'

export interface FGNode extends GNode {
  x?: number
  y?: number
  fx?: number
  fy?: number
}

export interface FGLink {
  id: string
  type: string
  source: string | FGNode
  target: string | FGNode
  props: Record<string, unknown>
}

export interface GraphData {
  nodes: FGNode[]
  links: FGLink[]
}

const MAX_NODES = 450

/**
 * Accumulates subgraphs from tool results into one force-graph dataset.
 * Node objects keep their identity across merges so the simulation doesn't restart from scratch.
 */
export function useGraph() {
  const nodes = useRef(new Map<string, FGNode>())
  const links = useRef(new Map<string, FGLink>())
  const order = useRef<string[]>([])
  const [data, setData] = useState<GraphData>({ nodes: [], links: [] })
  const [highlight, setHighlight] = useState<Set<string>>(new Set())

  const publish = useCallback(() => {
    setData({ nodes: [...nodes.current.values()], links: [...links.current.values()] })
  }, [])

  const merge = useCallback(
    (sg: SubGraph) => {
      for (const n of sg.nodes) {
        const existing = nodes.current.get(n.id)
        if (existing) {
          Object.assign(existing.props, n.props)
        } else {
          nodes.current.set(n.id, { ...n, props: { ...n.props } })
          order.current.push(n.id)
        }
      }
      for (const e of sg.edges) addLink(links.current, e)
      evict(nodes.current, links.current, order.current)
      publish()
    },
    [publish],
  )

  const reset = useCallback(
    (sg?: SubGraph) => {
      nodes.current.clear()
      links.current.clear()
      order.current = []
      setHighlight(new Set())
      if (sg) merge(sg)
      else publish()
    },
    [merge, publish],
  )

  return { data, merge, reset, highlight, setHighlight }
}

function addLink(map: Map<string, FGLink>, e: GEdge) {
  if (!map.has(e.id)) map.set(e.id, { id: e.id, type: e.type, source: e.source, target: e.target, props: e.props })
}

/** Keep the canvas readable: drop the oldest nodes (and their links) past MAX_NODES. */
function evict(nodes: Map<string, FGNode>, links: Map<string, FGLink>, order: string[]) {
  while (nodes.size > MAX_NODES && order.length) {
    const id = order.shift()!
    nodes.delete(id)
  }
  for (const [id, l] of links) {
    const s = typeof l.source === 'string' ? l.source : l.source.id
    const t = typeof l.target === 'string' ? l.target : l.target.id
    if (!nodes.has(s) || !nodes.has(t)) links.delete(id)
  }
}

export function linkEnds(l: FGLink): [string, string] {
  return [typeof l.source === 'string' ? l.source : l.source.id, typeof l.target === 'string' ? l.target : l.target.id]
}
