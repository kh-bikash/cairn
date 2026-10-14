import type { AgentEvent, Health, Stats, SubGraph, ToolResponse } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => json<Health>('/api/health'),
  stats: () => json<Stats>('/api/stats'),
  overview: () => json<SubGraph>('/api/graph/overview'),
  node: (id: string) =>
    json<{ id: string; label: string; props: Record<string, unknown>; neighbors: SubGraph }>(
      `/api/node?id=${encodeURIComponent(id)}`,
    ),
  tool: <T = Record<string, unknown>>(name: string, args: Record<string, unknown> = {}) =>
    json<ToolResponse<T>>(`/api/tools/${name}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ args }),
    }),
}

/**
 * Stream agent events. EventSource only supports GET, so we read the SSE body from a POST manually.
 */
export async function ask(
  question: string,
  history: { role: string; content: string }[],
  onEvent: (e: AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/api/ask`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ question, history }),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`ask failed: ${res.status} ${res.statusText}`)
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const data = frame
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
        .join('\n')
      if (data) onEvent(JSON.parse(data) as AgentEvent)
    }
  }
}
