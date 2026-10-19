import type { Point, Rect } from '@shared/types'
import { getDoc } from '@/state/app'

/** Zonas de la selección de texto actual, por página y en puntos de página. */
export interface SelectedText {
  text: string
  pages: Map<string, Rect[]>
  /** Posición en pantalla del final de la selección (para colocar el menú). */
  anchor: { x: number; y: number }
}

/** Une los rectángulos que están en la misma línea y se tocan. */
function mergeLine(rects: Rect[]): Rect[] {
  const sorted = [...rects].sort((a, b) => a[1] - b[1] || a[0] - b[0])
  const out: Rect[] = []
  for (const rect of sorted) {
    const last = out[out.length - 1]
    const height = rect[3] - rect[1]
    if (last && Math.abs(last[1] - rect[1]) < height * 0.5 && Math.abs(last[3] - rect[3]) < height * 0.5 && rect[0] <= last[2] + height * 0.6) {
      last[2] = Math.max(last[2], rect[2])
      last[1] = Math.min(last[1], rect[1])
      last[3] = Math.max(last[3], rect[3])
    } else {
      out.push([...rect])
    }
  }
  return out
}

export function readSelection(docId: string): SelectedText | null {
  const selection = window.getSelection()
  const doc = getDoc(docId)
  if (!selection || selection.isCollapsed || selection.rangeCount === 0 || !doc) return null
  const range = selection.getRangeAt(0)
  const pages = new Map<string, Rect[]>()
  let last: DOMRect | null = null
  for (const layer of document.querySelectorAll<HTMLElement>('.text-layer')) {
    if (!selection.containsNode(layer, true)) continue
    const pageId = layer.dataset.page
    const info = doc.pages.find((p) => p.id === pageId)
    const host = layer.closest<HTMLElement>('.page')
    if (!pageId || !info || !host) continue
    const bounds = host.getBoundingClientRect()
    const scale = bounds.width / info.w
    const rects: Rect[] = []
    for (const span of layer.querySelectorAll('span')) {
      if (!selection.containsNode(span, true)) continue
      // Se mide solo la parte seleccionada de cada palabra.
      const part = document.createRange()
      part.selectNodeContents(span)
      if (span.contains(range.startContainer)) part.setStart(range.startContainer, range.startOffset)
      if (span.contains(range.endContainer)) part.setEnd(range.endContainer, range.endOffset)
      const box = part.getBoundingClientRect()
      if (box.width < 0.6 || box.height < 0.6) continue
      last = box
      rects.push([
        (box.left - bounds.left) / scale,
        (box.top - bounds.top) / scale,
        (box.right - bounds.left) / scale,
        (box.bottom - bounds.top) / scale
      ])
    }
    if (rects.length) pages.set(pageId, mergeLine(rects))
  }
  if (pages.size === 0 || !last) return null
  return { text: selection.toString(), pages, anchor: { x: last.right, y: last.bottom } }
}

export function rectToQuad(rect: Rect): Point[] {
  return [
    [rect[0], rect[1]],
    [rect[2], rect[1]],
    [rect[0], rect[3]],
    [rect[2], rect[3]]
  ]
}
