import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: unknown
  loading: boolean
  reload: () => void
}

/** Run an async loader whenever `deps` change; ignores results from stale calls. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps)
  const reload = useCallback(() => {
    const id = ++seq.current
    setLoading(true)
    setError(null)
    run()
      .then((d) => {
        if (id === seq.current) setData(d)
      })
      .catch((e) => {
        if (id === seq.current) setError(e)
      })
      .finally(() => {
        if (id === seq.current) setLoading(false)
      })
  }, [run])
  useEffect(() => {
    reload()
  }, [reload])
  return { data, error, loading, reload }
}
