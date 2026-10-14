import type { GNode } from '../types'

export const COLORS = {
  person: '#f5b14c',
  repo: '#2dd4bf',
  module: '#5eead4',
  pr: '#818cf8',
  issue: '#f472b6',
  discussion: '#c084fc',
  doc: '#94a3b8',
  comment: '#64748b',
  decision: '#34d399',
  drift: '#f87171',
  premise: '#a3e635',
  answer: '#e2e8f0',
} as const

export function nodeColor(n: GNode): string {
  const p = n.props
  switch (n.label) {
    case 'Person':
      return COLORS.person
    case 'System':
      return p.kind === 'repo' ? COLORS.repo : COLORS.module
    case 'Decision':
      return p.drift ? COLORS.drift : COLORS.decision
    case 'Premise':
      return COLORS.premise
    case 'Source':
      return (COLORS as Record<string, string>)[String(p.kind)] ?? COLORS.comment
    default:
      return COLORS.answer
  }
}

export function nodeRadius(n: GNode): number {
  if (n.label === 'System') return n.props.kind === 'repo' ? 9 : 5
  if (n.label === 'Decision') return 7
  if (n.label === 'Person') return 5.5
  if (n.label === 'Premise') return 4
  return 3.5
}

export function nodeCaption(n: GNode): string {
  const p = n.props
  switch (n.label) {
    case 'Person':
      return `@${p.handle ?? ''}`
    case 'System': {
      const key = String(p.key ?? '')
      if (p.kind === 'repo') return key.split('/').pop() ?? key
      return key.split(':').pop() ?? key
    }
    case 'Source': {
      const uid = String(p.uid ?? '')
      const hash = uid.match(/#(\d+)(?:\/|$)/)
      if (p.kind === 'doc') return uid.split(':').pop() ?? uid
      return hash ? `#${hash[1]}` : uid
    }
    case 'Decision':
      return truncate(String(p.title ?? 'decision'), 34)
    case 'Premise':
      return truncate(String(p.text ?? 'premise'), 30)
    default:
      return n.id
  }
}

export function nodeTitle(n: GNode): string {
  const p = n.props
  return String(p.title ?? p.name ?? p.text ?? p.key ?? p.handle ?? n.id)
}

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

export const LEGEND: { label: string; color: string }[] = [
  { label: 'Person', color: COLORS.person },
  { label: 'Repo', color: COLORS.repo },
  { label: 'Module', color: COLORS.module },
  { label: 'PR', color: COLORS.pr },
  { label: 'Issue', color: COLORS.issue },
  { label: 'Decision', color: COLORS.decision },
  { label: 'Drifted', color: COLORS.drift },
  { label: 'Premise', color: COLORS.premise },
]
