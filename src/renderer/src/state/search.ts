import type { SearchResult } from '@shared/types'
import { isCancelled, query } from '@/lib/api'
import { viewerOf } from '@/lib/viewerControl'
import { getDoc, type SearchHitFlat, useApp } from './app'

const running = new Map<string, AbortController>()
const timers = new Map<string, number>()

function boundsOf(hit: SearchHitFlat): [number, number, number, number] {
  const xs = hit.quad.map((p) => p[0])
  const ys = hit.quad.map((p) => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}

function show(docId: string, index: number): void {
  const doc = getDoc(docId)
  const hit = doc?.search.hits[index]
  if (!hit) return
  useApp.getState().patchSearch(docId, { index })
  viewerOf(docId)?.reveal(hit.page, boundsOf(hit))
}

export function openSearch(docId: string): void {
  useApp.getState().patchSearch(docId, { open: true })
}

export function closeSearch(docId: string): void {
  running.get(docId)?.abort()
  useApp.getState().patchSearch(docId, { open: false, hits: [], byPage: {}, index: -1, busy: false, truncated: false })
}

async function run(docId: string): Promise<void> {
  const doc = getDoc(docId)
  if (!doc) return
  running.get(docId)?.abort()
  const text = doc.search.query.trim()
  if (text.length === 0) {
    useApp.getState().patchSearch(docId, { hits: [], byPage: {}, index: -1, busy: false, truncated: false })
    return
  }
  const controller = new AbortController()
  running.set(docId, controller)
  useApp.getState().patchSearch(docId, { busy: true })
  try {
    const result = await query<SearchResult>(
      'doc.search',
      { doc: docId, query: text, case: doc.search.matchCase, whole: doc.search.wholeWord },
      { signal: controller.signal, priority: 7 }
    )
    if (controller.signal.aborted) return
    const hits: SearchHitFlat[] = []
    const byPage: Record<string, number[]> = {}
    for (const group of result.hits) {
      for (const quad of group.quads) {
        ;(byPage[group.page] ??= []).push(hits.length)
        hits.push({ page: group.page, quad })
      }
    }
    // Se empieza por el primer acierto a partir de la página que se está viendo.
    const current = getDoc(docId)
    const order = new Map((current?.pages ?? []).map((p, i) => [p.id, i]))
    const from = order.get(current?.current ?? '') ?? 0
    let index = hits.findIndex((hit) => (order.get(hit.page) ?? 0) >= from)
    if (index < 0 && hits.length > 0) index = 0
    useApp.getState().patchSearch(docId, { hits, byPage, index, busy: false, truncated: result.truncated })
    if (index >= 0) show(docId, index)
  } catch (error) {
    if (!isCancelled(error)) useApp.getState().patchSearch(docId, { hits: [], byPage: {}, index: -1, busy: false })
  } finally {
    if (running.get(docId) === controller) running.delete(docId)
  }
}

/** Cambia el texto u opciones de búsqueda; la búsqueda se lanza al dejar de teclear. */
export function setSearch(docId: string, patch: { query?: string; matchCase?: boolean; wholeWord?: boolean }): void {
  useApp.getState().patchSearch(docId, patch)
  window.clearTimeout(timers.get(docId))
  timers.set(
    docId,
    window.setTimeout(() => void run(docId), patch.query === undefined ? 0 : 220)
  )
}

export function stepSearch(docId: string, direction: 1 | -1): void {
  const doc = getDoc(docId)
  if (!doc || doc.search.hits.length === 0) return
  const count = doc.search.hits.length
  show(docId, (doc.search.index + direction + count) % count)
}

/** Tras modificar el documento los aciertos pueden haber cambiado de sitio: se repite la búsqueda. */
export function refreshSearch(docId: string): void {
  const doc = getDoc(docId)
  if (doc?.search.open && doc.search.query.trim()) void run(docId)
}
