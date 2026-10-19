import {
  memo,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState
} from 'react'
import { cn } from '@/lib/util'

/** Caja manipulable sobre la página. Las medidas van en puntos de página. */
export interface Box {
  id: string
  cx: number
  cy: number
  w: number
  h: number
  /** Giro en grados (sentido horario). */
  angle: number
  /** 'aspect' mantiene la proporción al arrastrar las esquinas. */
  resize?: boolean | 'aspect'
  rotate?: boolean
  movable?: boolean
  className?: string
}

export type BoxChange = 'move' | 'resize' | 'rotate'

interface Props {
  boxes: Box[]
  pxPerPt: number
  selected: string[]
  onSelect: (id: string, additive: boolean) => void
  onChange: (id: string, next: Box, change: BoxChange) => void
  onActivate?: (id: string) => void
  onContext?: (id: string, event: ReactMouseEvent) => void
  render?: (box: Box) => ReactNode
  /** Las cajas sin seleccionar también se marcan (p. ej. campos de formulario en diseño). */
  outline?: boolean
}

const MIN = 5
const HANDLES: [string, number, number][] = [
  ['nw', -1, -1],
  ['n', 0, -1],
  ['ne', 1, -1],
  ['e', 1, 0],
  ['se', 1, 1],
  ['s', 0, 1],
  ['sw', -1, 1],
  ['w', -1, 0]
]

function rotate(x: number, y: number, degrees: number): [number, number] {
  const rad = (degrees * Math.PI) / 180
  const cos = Math.cos(rad)
  const sin = Math.sin(rad)
  return [x * cos - y * sin, x * sin + y * cos]
}

export const BoxLayer = memo(function BoxLayer({
  boxes,
  pxPerPt,
  selected,
  onSelect,
  onChange,
  onActivate,
  onContext,
  render,
  outline
}: Props) {
  const [draft, setDraft] = useState<Box | null>(null)
  const draftRef = useRef<Box | null>(null)
  const layer = useRef<HTMLDivElement>(null)

  // Si la caja en edición desaparece (deshacer, borrado), se abandona el arrastre.
  useEffect(() => {
    if (draft && !boxes.some((box) => box.id === draft.id)) setDraft(null)
  }, [boxes, draft])

  const begin = (event: ReactPointerEvent, box: Box, handle: string): void => {
    if (event.button !== 0) return
    event.stopPropagation()
    event.preventDefault()
    onSelect(box.id, event.shiftKey && handle === 'move')
    if (handle === 'move' && box.movable === false) return
    const start = { x: event.clientX, y: event.clientY }
    const origin = layer.current?.getBoundingClientRect()
    let moved = false
    const spec = HANDLES.find(([name]) => name === handle)

    const apply = (e: PointerEvent): void => {
      const dx = (e.clientX - start.x) / pxPerPt
      const dy = (e.clientY - start.y) / pxPerPt
      if (!moved && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 3) return
      moved = true
      let next: Box = { ...box }
      if (handle === 'move') {
        next.cx = box.cx + dx
        next.cy = box.cy + dy
      } else if (handle === 'rot' && origin) {
        const px = (e.clientX - origin.left) / pxPerPt
        const py = (e.clientY - origin.top) / pxPerPt
        let angle = (Math.atan2(py - box.cy, px - box.cx) * 180) / Math.PI + 90
        angle = ((angle % 360) + 360) % 360
        const step = e.shiftKey ? 15 : 0
        if (step) angle = Math.round(angle / step) * step
        // Imán a los ángulos rectos.
        for (const target of [0, 90, 180, 270, 360]) if (Math.abs(angle - target) < 3) angle = target % 360
        next.angle = angle
      } else if (spec) {
        const [, hx, hy] = spec
        const [lx, ly] = rotate(dx, dy, -box.angle)
        let w = Math.max(MIN, box.w + hx * lx)
        let h = Math.max(MIN, box.h + hy * ly)
        const lock = box.resize === 'aspect' ? !e.shiftKey : e.shiftKey
        if (lock && hx !== 0 && hy !== 0) {
          const k = Math.max(w / box.w, h / box.h)
          w = box.w * k
          h = box.h * k
        } else if (lock && box.resize === 'aspect') {
          const k = hx !== 0 ? w / box.w : h / box.h
          w = box.w * k
          h = box.h * k
        }
        const [sx, sy] = rotate((hx * (w - box.w)) / 2, (hy * (h - box.h)) / 2, box.angle)
        next = { ...next, w, h, cx: box.cx + sx, cy: box.cy + sy }
      }
      draftRef.current = next
      setDraft(next)
    }
    const end = (): void => {
      window.removeEventListener('pointermove', apply)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      const result = draftRef.current
      draftRef.current = null
      if (moved && result) {
        onChange(box.id, result, handle === 'move' ? 'move' : handle === 'rot' ? 'rotate' : 'resize')
        // La caja provisional se mantiene hasta que llegan los datos nuevos.
        window.setTimeout(() => setDraft((current) => (current === result ? null : current)), 900)
      } else {
        setDraft(null)
      }
    }
    window.addEventListener('pointermove', apply)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
  }

  const single = selected.length === 1
  return (
    <div ref={layer} className="box-layer">
      {boxes.map((source) => {
        const box = draft && draft.id === source.id ? draft : source
        const isSelected = selected.includes(source.id)
        const dragging = draft?.id === source.id
        return (
          <div
            key={source.id}
            className={cn('box', outline && 'outline', isSelected && 'selected', dragging && 'dragging', source.className)}
            style={{
              left: (box.cx - box.w / 2) * pxPerPt,
              top: (box.cy - box.h / 2) * pxPerPt,
              width: box.w * pxPerPt,
              height: box.h * pxPerPt,
              transform: box.angle ? `rotate(${box.angle}deg)` : undefined
            }}
            onPointerDown={(event) => begin(event, source, 'move')}
            onDoubleClick={(event) => {
              event.stopPropagation()
              onActivate?.(source.id)
            }}
            onContextMenu={(event) => {
              if (!onContext) return
              event.preventDefault()
              event.stopPropagation()
              onSelect(source.id, false)
              onContext(source.id, event)
            }}
          >
            {render?.(box)}
            {isSelected && single && (
              <>
                {source.resize !== false &&
                  HANDLES.map(([name]) => (
                    <span key={name} className={`handle ${name}`} onPointerDown={(event) => begin(event, source, name)} />
                  ))}
                {source.rotate && (
                  <>
                    <span className="rot-stem" />
                    <span className="handle rot" onPointerDown={(event) => begin(event, source, 'rot')} />
                  </>
                )}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
})
