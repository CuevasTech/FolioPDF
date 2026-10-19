import type { MutationResult, PlacedObject, Point, Rect, TextStyle } from '@shared/types'
import { unwrap } from '@/lib/api'
import { loadPageData } from '@/lib/pageData'
import { viewerOf } from '@/lib/viewerControl'
import { mutate, setTool, toastError } from '@/state/actions'
import { getDoc, useApp } from '@/state/app'

type ObjectResult = MutationResult & { object?: PlacedObject }

export function selectObject(doc: string, page: string, id: string | null): void {
  useApp.getState().patchDoc(doc, { selection: id ? { kind: 'object', page, ids: [id] } : null })
}

async function finish(doc: string, page: string, result: ObjectResult | null, keepTool = false): Promise<PlacedObject | null> {
  if (!result?.object) return null
  if (!keepTool) setTool(doc, '')
  selectObject(doc, page, result.object.id)
  return result.object
}

export async function addText(doc: string, page: string, at: Point, text: string, style: TextStyle): Promise<void> {
  if (!text.trim()) return
  const result = await mutate<ObjectResult>(doc, 'obj.addText', { page, x: at[0], y: at[1], text, style })
  await finish(doc, page, result)
}

export async function addShape(
  doc: string,
  page: string,
  shape: string,
  rect: Rect,
  options: { stroke: number[] | null; fill: number[] | null; width: number; angle?: number },
  keepTool = false
): Promise<void> {
  const result = await mutate<ObjectResult>(doc, 'obj.addShape', { page, shape, rect, ...options })
  await finish(doc, page, result, keepTool)
}

/** Coloca una imagen (PNG/JPEG en bruto) dentro del rectángulo indicado. */
export async function addImage(
  doc: string,
  page: string,
  data: Uint8Array,
  rect: Rect,
  kind: 'image' | 'signature' | 'stamp' = 'image',
  keepTool = false
): Promise<PlacedObject | null> {
  const result = await mutate<ObjectResult>(doc, 'obj.addImage', { page, rect, kind }, { binary: data })
  return finish(doc, page, result, keepTool)
}

/** Pide una imagen al usuario y la coloca centrada en la zona visible. */
export async function insertImageFromFile(docId: string): Promise<void> {
  try {
    const picked = unwrap(await window.folio.files.pickImage())
    if (!picked) return
    const doc = getDoc(docId)
    const centre = viewerOf(docId)?.center()
    if (!doc || !centre) return
    const page = doc.pages.find((p) => p.id === centre.page)
    if (!page) return
    let width = 240
    let height = 180
    try {
      const bitmap = await createImageBitmap(new Blob([picked.data as Uint8Array<ArrayBuffer>]))
      // Tamaño natural a 96 ppp, sin pasar de un 60 % de la página.
      const scale = Math.min(1, (page.w * 0.6) / (bitmap.width * 0.75), (page.h * 0.6) / (bitmap.height * 0.75))
      width = bitmap.width * 0.75 * scale
      height = bitmap.height * 0.75 * scale
      bitmap.close()
    } catch {
      /* formato que el navegador no previsualiza (p. ej. TIFF): tamaño por defecto */
    }
    const x = Math.min(Math.max(0, centre.x - width / 2), Math.max(0, page.w - width))
    const y = Math.min(Math.max(0, centre.y - height / 2), Math.max(0, page.h - height))
    await addImage(docId, page.id, picked.data, [x, y, x + width, y + height])
  } catch (error) {
    toastError(error)
  }
}

export async function deleteSelection(docId: string): Promise<boolean> {
  const doc = getDoc(docId)
  const selection = doc?.selection
  if (!doc || !selection || selection.ids.length === 0) return false
  const { page, ids } = selection
  useApp.getState().patchDoc(docId, { selection: null })
  if (selection.kind === 'object') await mutate(docId, 'obj.delete', { page, ids })
  else if (selection.kind === 'annot') await mutate(docId, 'annot.delete', { page, ids })
  else await mutate(docId, 'form.deleteFields', { page, ids })
  return true
}

/** Mueve con las flechas el elemento seleccionado. */
export async function nudgeSelection(docId: string, dx: number, dy: number): Promise<boolean> {
  const doc = getDoc(docId)
  const selection = doc?.selection
  if (!doc || !selection || selection.ids.length !== 1) return false
  const page = doc.pages.find((p) => p.id === selection.page)
  if (!page) return false
  const id = selection.ids[0]
  if (selection.kind === 'object') {
    const data = await loadPageData<{ objects: PlacedObject[] }>(docId, page.id, page.gen, 'objects')
    const object = data.objects.find((o) => o.id === id)
    if (!object) return false
    await mutate(docId, 'obj.transform', { page: page.id, id, cx: object.cx + dx, cy: object.cy + dy })
  } else if (selection.kind === 'annot') {
    const result = await mutate<MutationResult & { annot?: { id: string } }>(docId, 'annot.update', {
      page: page.id,
      id,
      move: [dx, dy]
    })
    // Trazos y líneas se recrean al moverse: la selección sigue al nuevo.
    if (result?.annot && result.annot.id !== id) {
      useApp.getState().patchDoc(docId, { selection: { kind: 'annot', page: page.id, ids: [result.annot.id] } })
    }
  } else {
    const data = await loadPageData<{ widgets: { id: string; rect: Rect }[] }>(docId, page.id, page.gen, 'widgets')
    const widget = data.widgets.find((w) => w.id === id)
    if (!widget) return false
    const [x0, y0, x1, y1] = widget.rect
    await mutate(docId, 'form.updateField', { page: page.id, id, rect: [x0 + dx, y0 + dy, x1 + dx, y1 + dy] })
  }
  return true
}
