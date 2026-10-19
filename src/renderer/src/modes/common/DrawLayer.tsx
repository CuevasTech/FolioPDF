import { type PointerEvent as ReactPointerEvent, useRef, useState } from 'react'
import type { Point, Rect } from '@shared/types'

/**
 * Capa que captura el gesto de una herramienta de creación sobre la página:
 * un clic (punto), un arrastre (rectángulo o línea) o un trazo a mano alzada.
 * Devuelve las coordenadas en puntos de página.
 */

type Gesture = 'point' | 'rect' | 'line' | 'ink'

interface Props {
  pxPerPt: number
  gesture: Gesture
  cursor?: string
  color?: string
  /** Grosor del trazo de vista previa, en puntos. */
  width?: number
  /** Relleno de la vista previa del rectángulo. */
  fill?: string
  shape?: 'rect' | 'ellipse'
  /** Con `rect`, un clic sin arrastrar también cuenta (se devuelve como punto). */
  clickToo?: boolean
  onPoint?: (point: Point) => void
  onRect?: (rect: Rect) => void
  onLine?: (a: Point, b: Point) => void
  onInk?: (stroke: Point[]) => void
}

/** Reduce los puntos de un trazo conservando su forma (Ramer–Douglas–Peucker). */
export function simplify(points: Point[], tolerance: number): Point[] {
  if (points.length < 3) return points
  const keep = new Uint8Array(points.length)
  keep[0] = keep[points.length - 1] = 1
  const stack: [number, number][] = [[0, points.length - 1]]
  while (stack.length) {
    const [first, last] = stack.pop() as [number, number]
    const [ax, ay] = points[first]
    const [bx, by] = points[last]
    const length = Math.hypot(bx - ax, by - ay) || 1
    let worst = 0
    let index = -1
    for (let i = first + 1; i < last; i++) {
      const distance = Math.abs((by - ay) * points[i][0] - (bx - ax) * points[i][1] + bx * ay - by * ax) / length
      if (distance > worst) {
        worst = distance
        index = i
      }
    }
    if (index > 0 && worst > tolerance) {
      keep[index] = 1
      stack.push([first, index], [index, last])
    }
  }
  return points.filter((_, i) => keep[i])
}

export function DrawLayer({
  pxPerPt,
  gesture,
  cursor = 'crosshair',
  color = 'var(--accent-fg)',
  width = 1.5,
  fill,
  shape = 'rect',
  clickToo,
  onPoint,
  onRect,
  onLine,
  onInk
}: Props) {
  const host = useRef<HTMLDivElement>(null)
  const [start, setStart] = useState<Point | null>(null)
  const [current, setCurrent] = useState<Point | null>(null)
  const [stroke, setStroke] = useState<Point[] | null>(null)
  const strokeRef = useRef<Point[]>([])

  const toPage = (event: { clientX: number; clientY: number }): Point => {
    const bounds = host.current?.getBoundingClientRect()
    if (!bounds) return [0, 0]
    const x = Math.min(bounds.width, Math.max(0, event.clientX - bounds.left))
    const y = Math.min(bounds.height, Math.max(0, event.clientY - bounds.top))
    return [x / pxPerPt, y / pxPerPt]
  }

  const down = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0) return
    event.preventDefault()
    event.stopPropagation()
    const origin = toPage(event)
    if (gesture === 'point') {
      onPoint?.(origin)
      return
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setStart(origin)
    setCurrent(origin)
    if (gesture === 'ink') {
      strokeRef.current = [origin]
      setStroke([origin])
    }
  }

  const move = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (!start) return
    if (gesture === 'ink') {
      // Los eventos agrupados dan un trazo más fiel cuando el ratón va rápido.
      const samples = event.nativeEvent.getCoalescedEvents?.() ?? [event.nativeEvent]
      for (const sample of samples) strokeRef.current.push(toPage(sample))
      setStroke([...strokeRef.current])
    } else {
      setCurrent(toPage(event))
    }
  }

  const up = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (!start) return
    const end = toPage(event)
    setStart(null)
    setCurrent(null)
    if (gesture === 'ink') {
      const points = simplify(strokeRef.current, 0.35)
      strokeRef.current = []
      setStroke(null)
      if (points.length > 0) onInk?.(points)
      return
    }
    const dragged = Math.hypot(end[0] - start[0], end[1] - start[1]) * pxPerPt > 4
    if (gesture === 'line') {
      if (dragged) onLine?.(start, end)
    } else if (dragged) {
      onRect?.([Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])])
    } else if (clickToo) {
      onPoint?.(start)
    }
  }

  const px = (value: number): number => value * pxPerPt
  return (
    <div
      ref={host}
      className="draw-layer"
      style={{ cursor }}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={() => {
        setStart(null)
        setCurrent(null)
        setStroke(null)
      }}
    >
      {start && current && gesture === 'rect' && (
        <div
          className="draw-preview"
          style={{
            left: px(Math.min(start[0], current[0])),
            top: px(Math.min(start[1], current[1])),
            width: px(Math.abs(current[0] - start[0])),
            height: px(Math.abs(current[1] - start[1])),
            borderColor: color,
            background: fill,
            borderRadius: shape === 'ellipse' ? '50%' : 2
          }}
        />
      )}
      {((start && current && gesture === 'line') || stroke) && (
        <svg className="draw-svg">
          {stroke ? (
            <polyline
              points={stroke.map(([x, y]) => `${px(x)},${px(y)}`).join(' ')}
              fill="none"
              stroke={color}
              strokeWidth={Math.max(1, px(width))}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : (
            start &&
            current && (
              <line
                x1={px(start[0])}
                y1={px(start[1])}
                x2={px(current[0])}
                y2={px(current[1])}
                stroke={color}
                strokeWidth={Math.max(1, px(width))}
                strokeLinecap="round"
              />
            )
          )}
        </svg>
      )}
    </div>
  )
}
