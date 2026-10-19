import { create } from 'zustand'
import type { SavedSignature } from '@shared/api'
import { L } from '@/i18n'

/** Biblioteca de firmas manuscritas (guardada cifrada por el proceso principal). */
interface SignatureStore {
  list: SavedSignature[]
  urls: Record<string, string>
  active: string | null
  loaded: boolean
  load(): Promise<void>
  setList(list: SavedSignature[]): void
  setActive(id: string | null): void
}

export const useSignatures = create<SignatureStore>()((set, get) => ({
  list: [],
  urls: {},
  active: null,
  loaded: false,
  load: async () => {
    if (get().loaded) return
    get().setList(await window.folio.signatures.list())
    set({ loaded: true })
  },
  setList: (list) => {
    const previous = get().urls
    const urls: Record<string, string> = {}
    for (const item of list) {
      urls[item.id] = previous[item.id] ?? URL.createObjectURL(new Blob([item.png as Uint8Array<ArrayBuffer>], { type: 'image/png' }))
    }
    for (const [id, url] of Object.entries(previous)) if (!urls[id]) URL.revokeObjectURL(url)
    set({ list, urls, active: list.some((s) => s.id === get().active) ? get().active : (list[0]?.id ?? null) })
  },
  setActive: (active) => set({ active })
}))

export interface SignatureImage {
  png: Uint8Array
  width: number
  height: number
}

/** Recorta el lienzo a la tinta que contiene y lo devuelve como PNG con transparencia. */
export async function exportTrimmed(canvas: HTMLCanvasElement, padding = 8): Promise<SignatureImage | null> {
  const context = canvas.getContext('2d')
  if (!context) return null
  const { width, height } = canvas
  const pixels = context.getImageData(0, 0, width, height).data
  let left = width
  let top = height
  let right = -1
  let bottom = -1
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (pixels[(y * width + x) * 4 + 3] > 12) {
        if (x < left) left = x
        if (x > right) right = x
        if (y < top) top = y
        if (y > bottom) bottom = y
      }
    }
  }
  if (right < left || bottom < top) return null
  left = Math.max(0, left - padding)
  top = Math.max(0, top - padding)
  right = Math.min(width - 1, right + padding)
  bottom = Math.min(height - 1, bottom + padding)
  const out = document.createElement('canvas')
  out.width = right - left + 1
  out.height = bottom - top + 1
  out.getContext('2d')?.drawImage(canvas, left, top, out.width, out.height, 0, 0, out.width, out.height)
  const blob = await new Promise<Blob | null>((resolve) => out.toBlob(resolve, 'image/png'))
  if (!blob) return null
  return { png: new Uint8Array(await blob.arrayBuffer()), width: out.width, height: out.height }
}

/** Convierte una foto o escaneo de una firma en tinta sobre fondo transparente. */
export async function imageToInk(data: Uint8Array): Promise<SignatureImage | null> {
  const bitmap = await createImageBitmap(new Blob([data as Uint8Array<ArrayBuffer>]))
  const scale = Math.min(1, 1400 / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) return null
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  const image = context.getImageData(0, 0, canvas.width, canvas.height)
  const px = image.data
  for (let i = 0; i < px.length; i += 4) {
    // El papel (claro) se vuelve transparente; el trazo conserva su color.
    const luminance = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]
    const ink = Math.max(0, Math.min(1, (215 - luminance) / 90))
    px[i + 3] = Math.round(px[i + 3] * ink)
  }
  context.putImageData(image, 0, 0)
  return exportTrimmed(canvas)
}

export const SCRIPT_FONTS = ['Segoe Script', 'Ink Free', 'Lucida Handwriting', 'Gabriola', 'Segoe Print']
export const INKS: [string, string][] = [
  ['#101a4d', L.sign.pad.inkBlue],
  ['#111111', L.sign.pad.inkBlack]
]
