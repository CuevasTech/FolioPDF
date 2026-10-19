import { useEffect, useReducer, useRef } from 'react'
import { query } from './api'

/**
 * Datos por página que la interfaz pide al motor (texto, enlaces, elementos…).
 * Se guardan por documento, página y «generación»: cuando una página cambia,
 * su generación sube y los datos se vuelven a pedir.
 */

export type PageDataKind = 'text' | 'links' | 'objects' | 'annots' | 'widgets' | 'editables'

const METHODS: Record<PageDataKind, string> = {
  text: 'page.text',
  links: 'page.links',
  objects: 'page.objects',
  annots: 'page.annots',
  widgets: 'page.widgets',
  editables: 'page.editables'
}

interface Entry {
  promise: Promise<unknown>
  value?: unknown
  settled: boolean
}

const MAX_ENTRIES = 600
const cache = new Map<string, Entry>()

function keyOf(doc: string, page: string, gen: number, kind: PageDataKind): string {
  return `${doc}|${page}|${gen}|${kind}`
}

function ensure(doc: string, page: string, gen: number, kind: PageDataKind): Entry {
  const key = keyOf(doc, page, gen, kind)
  const found = cache.get(key)
  if (found) {
    // Reinsertar lo marca como usado recientemente.
    cache.delete(key)
    cache.set(key, found)
    return found
  }
  const entry: Entry = { promise: Promise.resolve(), settled: false }
  entry.promise = query(METHODS[kind], { doc, page }, { priority: 6 }).then(
    (value) => {
      entry.value = value
      entry.settled = true
      return value
    },
    (error) => {
      // Un fallo no se guarda: la próxima vez se vuelve a intentar.
      cache.delete(key)
      entry.settled = true
      throw error
    }
  )
  entry.promise.catch(() => undefined)
  cache.set(key, entry)
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string)
  return entry
}

export function loadPageData<T>(doc: string, page: string, gen: number, kind: PageDataKind): Promise<T> {
  return ensure(doc, page, gen, kind).promise as Promise<T>
}

/**
 * Datos de una página para un componente. Mientras llega una generación nueva
 * se sigue devolviendo la anterior, para que las capas no parpadeen tras cada cambio.
 */
export function usePageData<T>(doc: string, page: string, gen: number, kind: PageDataKind, enabled = true): T | undefined {
  const [, refresh] = useReducer((n: number) => n + 1, 0)
  const last = useRef<{ key: string; value: T } | null>(null)
  const entry = enabled ? ensure(doc, page, gen, kind) : null

  useEffect(() => {
    if (!entry || entry.settled) return
    let alive = true
    entry.promise.then(
      () => alive && refresh(),
      () => undefined
    )
    return () => {
      alive = false
    }
  }, [entry])

  const scope = `${doc}|${page}|${kind}`
  if (last.current && last.current.key !== scope) last.current = null
  if (entry?.value !== undefined) last.current = { key: scope, value: entry.value as T }
  return enabled ? last.current?.value : undefined
}

export function dropDocCaches(doc: string): void {
  const prefix = `${doc}|`
  for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key)
  for (const listener of dropListeners) listener(doc)
}

const dropListeners = new Set<(doc: string) => void>()

export function onDocDropped(listener: (doc: string) => void): void {
  dropListeners.add(listener)
}
