import type { PageInfo, Rect } from '@shared/types'
import { call } from './api'
import { onDocDropped } from './pageData'

/** Píxeles de pantalla por punto tipográfico a zoom 100 %. */
export const PT_TO_PX = 96 / 72

/** Tope de píxeles por página dibujada entera; por encima se dibuja solo la zona visible. */
export const PAGE_PIXEL_BUDGET = 18_000_000

interface RenderMeta {
  w: number
  h: number
  x: number
  y: number
}

export interface RenderOptions {
  clip?: Rect
  annots?: boolean
  priority?: number
  signal?: AbortSignal
}

/** Pide al motor una página (o un recorte) y la devuelve lista para pintar. */
export async function renderPage(doc: string, page: string, scale: number, options: RenderOptions = {}): Promise<ImageBitmap> {
  const params: Record<string, unknown> = { doc, page, scale }
  if (options.clip) params.clip = options.clip
  if (options.annots === false) params.annots = false
  const { result, binary } = await call<RenderMeta>('page.render', params, {
    priority: options.priority ?? 5,
    signal: options.signal
  })
  if (!binary || binary.byteLength !== result.w * result.h * 4) {
    throw new Error('respuesta de dibujo incompleta')
  }
  // Los píxeles llegan en RGBA opaco: se envuelven sin copiarlos.
  const pixels = new Uint8ClampedArray(binary.buffer, binary.byteOffset, binary.byteLength)
  return createImageBitmap(new ImageData(pixels as Uint8ClampedArray<ArrayBuffer>, result.w, result.h))
}

/** Escala máxima (px por punto) a la que cabe la página entera en el presupuesto de píxeles. */
export function maxFullScale(page: PageInfo): number {
  return Math.sqrt(PAGE_PIXEL_BUDGET / Math.max(1, page.w * page.h))
}

// ------------------------------------------------------------------ miniaturas

const THUMB_LIMIT = 400
const thumbs = new Map<string, Promise<ImageBitmap>>()

export function thumbKey(doc: string, page: PageInfo, width: number): string {
  return `${doc}|${page.id}|${page.gen}|${width}`
}

/** Miniatura de una página con el ancho indicado en píxeles reales. Se comparte entre vistas. */
export function getThumb(doc: string, page: PageInfo, width: number): Promise<ImageBitmap> {
  const key = thumbKey(doc, page, width)
  const found = thumbs.get(key)
  if (found) {
    thumbs.delete(key)
    thumbs.set(key, found)
    return found
  }
  const promise = renderPage(doc, page.id, width / page.w, { priority: 2 })
  promise.catch(() => thumbs.delete(key))
  thumbs.set(key, promise)
  while (thumbs.size > THUMB_LIMIT) {
    const oldest = thumbs.keys().next().value as string
    thumbs
      .get(oldest)
      ?.then((bitmap) => bitmap.close())
      .catch(() => undefined)
    thumbs.delete(oldest)
  }
  return promise
}

onDocDropped((doc) => {
  const prefix = `${doc}|`
  for (const [key, promise] of [...thumbs]) {
    if (!key.startsWith(prefix)) continue
    promise.then((bitmap) => bitmap.close()).catch(() => undefined)
    thumbs.delete(key)
  }
})
