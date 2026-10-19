import { memo, useEffect, useRef } from 'react'
import type { PageInfo, Rect } from '@shared/types'
import { maxFullScale, renderPage } from '@/lib/render'

interface Props {
  doc: string
  page: PageInfo
  /** Píxeles de pantalla por punto con el zoom actual. */
  pxPerPt: number
  priority: number
  /**
   * Zona visible de la página (en puntos). Solo se pasa cuando el zoom es tan
   * alto que la página entera no se dibuja a resolución completa; entonces se
   * superpone un recorte nítido de esa zona.
   */
  view: Rect | null
  annots?: boolean
}

function paint(canvas: HTMLCanvasElement, bitmap: ImageBitmap): void {
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  canvas.getContext('2d', { alpha: false })?.drawImage(bitmap, 0, 0)
  bitmap.close()
}

export const PageCanvas = memo(function PageCanvas({ doc, page, pxPerPt, priority, view, annots = true }: Props) {
  const baseRef = useRef<HTMLCanvasElement>(null)
  const patchRef = useRef<HTMLCanvasElement>(null)
  const drawn = useRef<{ gen: number; scale: number; annots: boolean } | null>(null)
  const priorityRef = useRef(priority)
  priorityRef.current = priority

  const dpr = window.devicePixelRatio || 1
  const wanted = pxPerPt * dpr
  const full = Math.min(wanted, maxFullScale(page))
  // Se redondea para que cambios mínimos de zoom no provoquen redibujados.
  const fullKey = Math.round(full * 200) / 200

  useEffect(() => {
    const previous = drawn.current
    const sameContent = previous?.gen === page.gen && previous.annots === annots
    if (previous && sameContent && Math.abs(previous.scale - fullKey) < 0.004) return
    const controller = new AbortController()
    // La primera vez y tras un cambio de contenido se dibuja ya; al hacer zoom
    // se espera a que el gesto termine (mientras, se estira lo que había).
    const delay = previous && sameContent ? 110 : 0
    const timer = window.setTimeout(() => {
      renderPage(doc, page.id, fullKey, { priority: priorityRef.current, signal: controller.signal, annots })
        .then((bitmap) => {
          const canvas = baseRef.current
          if (!canvas || controller.signal.aborted) {
            bitmap.close()
            return
          }
          paint(canvas, bitmap)
          drawn.current = { gen: page.gen, scale: fullKey, annots }
        })
        .catch(() => undefined)
    }, delay)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [doc, page.id, page.gen, fullKey, annots])

  // Recorte nítido de la zona visible a zoom alto.
  const needPatch = view !== null && wanted > full * 1.04
  const viewKey = needPatch && view ? view.map((v) => Math.round(v)).join(',') : ''
  useEffect(() => {
    const canvas = patchRef.current
    if (!canvas) return
    if (!needPatch || !view) {
      canvas.style.display = 'none'
      return
    }
    const margin = 24 / pxPerPt
    const clip: Rect = [
      Math.max(0, view[0] - margin),
      Math.max(0, view[1] - margin),
      Math.min(page.w, view[2] + margin),
      Math.min(page.h, view[3] + margin)
    ]
    if (clip[2] - clip[0] < 1 || clip[3] - clip[1] < 1) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      renderPage(doc, page.id, wanted, { clip, priority: priorityRef.current + 1, signal: controller.signal, annots })
        .then((bitmap) => {
          if (controller.signal.aborted) {
            bitmap.close()
            return
          }
          paint(canvas, bitmap)
          canvas.style.left = `${clip[0] * pxPerPt}px`
          canvas.style.top = `${clip[1] * pxPerPt}px`
          canvas.style.width = `${(clip[2] - clip[0]) * pxPerPt}px`
          canvas.style.height = `${(clip[3] - clip[1]) * pxPerPt}px`
          canvas.style.display = 'block'
        })
        .catch(() => undefined)
    }, 140)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
    // view se resume en viewKey, y pxPerPt ya va en wanted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, page.id, page.gen, viewKey, wanted, needPatch, annots])

  // Un recorte hecho para otro zoom u otro contenido ya no vale: se oculta al cambiar.
  useEffect(() => {
    const canvas = patchRef.current
    return () => {
      if (canvas) canvas.style.display = 'none'
    }
  }, [page.gen, pxPerPt])

  return (
    <>
      <canvas ref={baseRef} className="page-canvas" />
      <canvas ref={patchRef} className="page-patch" style={{ display: 'none' }} />
    </>
  )
})
