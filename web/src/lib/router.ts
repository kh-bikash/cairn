import { useEffect, useState } from 'react'

/** Tiny hash router: `#/decisions/<uid>` -> { page: 'decisions', id: '<uid>' }. Works on any static host. */
export interface Route {
  page: string
  id: string | null
  query: URLSearchParams
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '')
  const [path, query = ''] = raw.split('?')
  const [page, ...rest] = path.split('/')
  const id = rest.length ? decodeURIComponent(rest.join('/')) : null
  return { page: page || 'ask', id, query: new URLSearchParams(query) }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

export function href(page: string, id?: string | null, query?: Record<string, string>): string {
  const q = query ? `?${new URLSearchParams(query).toString()}` : ''
  return `#/${page}${id ? `/${encodeURIComponent(id)}` : ''}${q}`
}

export function go(page: string, id?: string | null, query?: Record<string, string>) {
  window.location.hash = href(page, id, query)
}

/** Where a source opens: in-app for imported sources, externally when it has a real URL. */
export function sourceHref(uid: string, url?: string | null): { href: string; external: boolean } {
  if (url && /^https?:\/\//.test(url)) return { href: url, external: true }
  return { href: href('source', uid), external: false }
}
