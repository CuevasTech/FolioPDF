import { type PointerEvent as ReactPointerEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { PageInfo, Rect } from '@shared/types'
import { maxFullScale, PT_TO_PX } from '@/lib/render'
import { clamp, cn, rafThrottle } from '@/lib/util'
import { nextZoom, registerViewer, ZOOM_MAX, ZOOM_MIN } from '@/lib/viewerControl'
import { type DocView, useApp } from '@/state/app'
import { type PageItem, PageView } from './PageView'

const GAP = 16
const PAD_X = 28
const PAD_TOP = 68 // deja sitio a la barra de herramientas flotante
const PAD_BOTTOM = 76 // y al indicador de página

interface Layout {
  items: PageItem[]
  index: Map<string, number>
  width: number
  height: number
}

/** Punto del documento que debe permanecer fijo en pantalla cuando cambia el diseño. */
interface Anchor {
  id: string
  fx: number
  fy: number
  sx: number
  sy: number
}

const savedAnchors = new Map<string, Anchor>()

function buildLayout(pages: PageInfo[], pxPerPt: number, viewportWidth: number): Layout {
  const widest = pages.reduce((max, p) => Math.max(max, p.w), 1) * pxPerPt
  const width = Math.max(viewportWidth, widest + PAD_X * 2)
  let y = PAD_TOP
  const items: PageItem[] = pages.map((page) => {
    const w = page.w * pxPerPt
    const h = page.h * pxPerPt
    const item = { id: page.id, x: Math.round((width - w) / 2), y: Math.round(y), w, h }
    y += h + GAP
    return item
  })
  return { items, index: new Map(items.map((item, i) => [item.id, i])), width, height: y - GAP + PAD_BOTTOM }
}

function effectiveZoom(zoom: DocView['zoom'], pages: PageInfo[], width: number, height: number): number {
  if (zoom.kind === 'custom' || width === 0) return zoom.scale
  const available = Math.max(160, width - PAD_X * 2 - 14)
  const widest = pages.reduce((max, p) => Math.max(max, p.w), 1)
  const fitWidth = available / (widest * PT_TO_PX)
  if (zoom.kind === 'fit-width') return clamp(fitWidth, ZOOM_MIN, ZOOM_MAX)
  const tallest = pages.reduce((max, p) => Math.max(max, p.h), 1)
  const fitHeight = Math.max(160, height - PAD_TOP - PAD_BOTTOM) / (tallest * PT_TO_PX)
  return clamp(Math.min(fitWidth, fitHeight), ZOOM_MIN, ZOOM_MAX)
}

/** Índice de la página que contiene la coordenada vertical (o la más cercana). */
function indexAt(items: PageItem[], y: number): number {
  let low = 0
  let high = items.length - 1
  while (low < high) {
    const mid = (low + high) >> 1
    if (items[mid].y + items[mid].h + GAP / 2 < y) low = mid + 1
    else high = mid
  }
  return low
}

export function Viewer({ doc }: { doc: DocView }) {
  const scroller = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState({ w: 0, h: 0 })
  const [scroll, setScroll] = useState({ top: 0, left: 0 })
  const [panning, setPanning] = useState(false)
  const anchor = useRef<Anchor | null>(savedAnchors.get(doc.id) ?? null)
  const patchDoc = useApp((s) => s.patchDoc)

  const zoom = effectiveZoom(doc.zoom, doc.pages, viewport.w, viewport.h)
  const pxPerPt = zoom * PT_TO_PX
  const layout = useMemo(() => buildLayout(doc.pages, pxPerPt, viewport.w), [doc.pages, pxPerPt, viewport.w])
  const layoutRef = useRef(layout)
  layoutRef.current = layout

  // El zoom efectivo (también en «ajustar al ancho») se publica para el indicador.
  useEffect(() => {
    if (viewport.w > 0 && Math.abs(doc.zoom.scale - zoom) > 0.0005) {
      patchDoc(doc.id, { zoom: { kind: doc.zoom.kind, scale: zoom } })
    }
  }, [doc.id, doc.zoom.kind, doc.zoom.scale, zoom, viewport.w, patchDoc])

  const capture = useCallback((sx?: number, sy?: number) => {
    const element = scroller.current
    const current = layoutRef.current
    if (!element || current.items.length === 0) return
    const px = sx ?? element.clientWidth / 2
    const py = sy ?? element.clientHeight / 2
    const item = current.items[indexAt(current.items, element.scrollTop + py)]
    anchor.current = {
      id: item.id,
      fx: (element.scrollLeft + px - item.x) / item.w,
      fy: (element.scrollTop + py - item.y) / item.h,
      sx: px,
      sy: py
    }
  }, [])

  useLayoutEffect(() => {
    const element = scroller.current
    if (!element) return
    const observer = new ResizeObserver(() => setViewport({ w: element.clientWidth, h: element.clientHeight }))
    observer.observe(element)
    setViewport({ w: element.clientWidth, h: element.clientHeight })
    return () => observer.disconnect()
  }, [])

  // Al cambiar el diseño (zoom, páginas, tamaño de ventana) el punto de anclaje vuelve a su sitio.
  useLayoutEffect(() => {
    const element = scroller.current
    const target = anchor.current
    if (!element || !target || viewport.w === 0) return
    const item = layout.items[layout.index.get(target.id) ?? -1]
    if (!item) return
    element.scrollLeft = item.x + target.fx * item.w - target.sx
    element.scrollTop = item.y + target.fy * item.h - target.sy
    setScroll({ top: element.scrollTop, left: element.scrollLeft })
  }, [layout, viewport.w])

  // Al salir (cambio de pestaña) se recuerda por dónde se iba leyendo.
  useEffect(() => {
    const id = doc.id
    // El visor recibe el foco para que las teclas de desplazamiento funcionen de entrada.
    scroller.current?.focus({ preventScroll: true })
    return () => {
      if (anchor.current) savedAnchors.set(id, anchor.current)
    }
  }, [doc.id])

  const onScroll = useMemo(
    () =>
      rafThrottle(() => {
        const element = scroller.current
        const current = layoutRef.current
        if (!element || current.items.length === 0) return
        capture()
        setScroll({ top: element.scrollTop, left: element.scrollLeft })
        const centre = current.items[indexAt(current.items, element.scrollTop + element.clientHeight * 0.4)]
        const state = useApp.getState()
        if (centre && state.docs[doc.id]?.current !== centre.id) state.patchDoc(doc.id, { current: centre.id })
      }),
    [capture, doc.id]
  )
  useEffect(() => () => onScroll.cancel(), [onScroll])

  // Órdenes que llegan desde otras partes de la interfaz.
  useEffect(() => {
    const element = scroller.current
    if (!element) return
    return registerViewer(doc.id, {
      scrollToPage(page, y) {
        const current = layoutRef.current
        const item = current.items[current.index.get(page) ?? -1]
        if (!item) return
        const scale = item.w / (useApp.getState().docs[doc.id]?.pages.find((p) => p.id === page)?.w ?? 1)
        element.scrollTop = y === undefined ? item.y - (PAD_TOP - 12) : item.y + y * scale - 96
        anchor.current = null
      },
      reveal(page, rect) {
        const current = layoutRef.current
        const item = current.items[current.index.get(page) ?? -1]
        const info = useApp.getState().docs[doc.id]?.pages.find((p) => p.id === page)
        if (!item || !info) return
        const scale = item.w / info.w
        const top = item.y + rect[1] * scale
        const bottom = item.y + rect[3] * scale
        const left = item.x + rect[0] * scale
        const right = item.x + rect[2] * scale
        if (top < element.scrollTop + PAD_TOP || bottom > element.scrollTop + element.clientHeight - PAD_BOTTOM) {
          element.scrollTop = (top + bottom) / 2 - element.clientHeight / 2
        }
        if (left < element.scrollLeft + 20 || right > element.scrollLeft + element.clientWidth - 20) {
          element.scrollLeft = (left + right) / 2 - element.clientWidth / 2
        }
        anchor.current = null
      },
      zoomStep(direction) {
        capture()
        const view = useApp.getState().docs[doc.id]
        if (view) useApp.getState().patchDoc(doc.id, { zoom: { kind: 'custom', scale: nextZoom(view.zoom.scale, direction) } })
      },
      center() {
        const current = layoutRef.current
        if (current.items.length === 0) return null
        const item = current.items[indexAt(current.items, element.scrollTop + element.clientHeight / 2)]
        const info = useApp.getState().docs[doc.id]?.pages.find((p) => p.id === item.id)
        if (!info) return null
        const scale = item.w / info.w
        return {
          page: item.id,
          x: clamp((element.scrollLeft + element.clientWidth / 2 - item.x) / scale, 0, info.w),
          y: clamp((element.scrollTop + element.clientHeight / 2 - item.y) / scale, 0, info.h)
        }
      }
    })
  }, [doc.id, capture])

  // Ctrl + rueda: zoom alrededor del cursor. Se registra a mano porque necesita
  // poder cancelar el desplazamiento (los oyentes de React son pasivos).
  useEffect(() => {
    const element = scroller.current
    if (!element) return
    const onWheel = (event: WheelEvent): void => {
      if (!event.ctrlKey) return
      event.preventDefault()
      const view = useApp.getState().docs[doc.id]
      if (!view) return
      const bounds = element.getBoundingClientRect()
      capture(event.clientX - bounds.left, event.clientY - bounds.top)
      const factor = Math.exp(-event.deltaY * 0.0022)
      const scale = clamp(view.zoom.scale * factor, ZOOM_MIN, ZOOM_MAX)
      useApp.getState().patchDoc(doc.id, { zoom: { kind: 'custom', scale } })
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [doc.id, capture])

  // Arrastre para desplazar: herramienta «mano» o botón central del ratón.
  const handTool = doc.mode === 'view' && doc.tool === 'hand'
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const element = scroller.current
    if (!element) return
    const middle = event.button === 1
    if (!(middle || (handTool && event.button === 0))) return
    event.preventDefault()
    const start = { x: event.clientX, y: event.clientY, top: element.scrollTop, left: element.scrollLeft }
    setPanning(true)
    const move = (e: PointerEvent): void => {
      element.scrollTop = start.top - (e.clientY - start.y)
      element.scrollLeft = start.left - (e.clientX - start.x)
    }
    const up = (): void => {
      setPanning(false)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // Páginas a la vista (más un margen para que estén listas al desplazarse).
  const overscan = viewport.h * 0.6
  const first = layout.items.length ? indexAt(layout.items, scroll.top - overscan) : 0
  let last = first
  while (last + 1 < layout.items.length && layout.items[last + 1].y < scroll.top + viewport.h + overscan) last++
  const visible = viewport.w > 0 ? layout.items.slice(first, last + 1) : []
  const pageById = useMemo(() => new Map(doc.pages.map((p) => [p.id, p])), [doc.pages])
  const dpr = window.devicePixelRatio || 1

  return (
    <div
      ref={scroller}
      className={cn('viewer', handTool && 'pan', panning && 'pan panning')}
      tabIndex={0}
      onScroll={onScroll}
      onPointerDown={onPointerDown}
      data-doc={doc.id}
    >
      <div className="viewer-content" style={{ width: layout.width, height: layout.height }}>
        {visible.map((item) => {
          const page = pageById.get(item.id)
          if (!page) return null
          const onScreen = item.y + item.h > scroll.top && item.y < scroll.top + viewport.h
          // Solo a zoom alto hace falta saber qué parte de la página se ve.
          let view: Rect | null = null
          if (onScreen && pxPerPt * dpr > maxFullScale(page) * 1.04) {
            view = [
              clamp((scroll.left - item.x) / pxPerPt, 0, page.w),
              clamp((scroll.top - item.y) / pxPerPt, 0, page.h),
              clamp((scroll.left + viewport.w - item.x) / pxPerPt, 0, page.w),
              clamp((scroll.top + viewport.h - item.y) / pxPerPt, 0, page.h)
            ]
          }
          return (
            <PageView
              key={item.id}
              doc={doc.id}
              page={page}
              item={item}
              pxPerPt={pxPerPt}
              priority={onScreen ? 5 : 3}
              view={view}
            />
          )
        })}
      </div>
    </div>
  )
}
