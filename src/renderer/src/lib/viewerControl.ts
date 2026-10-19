import type { Rect } from '@shared/types'

/** Órdenes que el resto de la interfaz puede dar al visor de un documento. */
export interface ViewerControl {
  scrollToPage(page: string, y?: number): void
  /** Desplaza lo justo para que el rectángulo (en puntos de la página) quede a la vista. */
  reveal(page: string, rect: Rect): void
  zoomStep(direction: 1 | -1): void
  /** Punto de la página que está en el centro de la vista (para colocar elementos nuevos). */
  center(): { page: string; x: number; y: number } | null
}

const controls = new Map<string, ViewerControl>()

export function registerViewer(doc: string, control: ViewerControl): () => void {
  controls.set(doc, control)
  return () => {
    if (controls.get(doc) === control) controls.delete(doc)
  }
}

export function viewerOf(doc: string): ViewerControl | undefined {
  return controls.get(doc)
}

export const ZOOM_STEPS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6.4]
export const ZOOM_MIN = ZOOM_STEPS[0]
export const ZOOM_MAX = ZOOM_STEPS[ZOOM_STEPS.length - 1]

export function nextZoom(current: number, direction: 1 | -1): number {
  if (direction > 0) return ZOOM_STEPS.find((step) => step > current * 1.01) ?? ZOOM_MAX
  return [...ZOOM_STEPS].reverse().find((step) => step < current * 0.99) ?? ZOOM_MIN
}
