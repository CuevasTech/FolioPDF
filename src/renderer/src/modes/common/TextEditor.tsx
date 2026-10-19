import { useEffect, useRef } from 'react'
import type { TextStyle } from '@shared/types'
import { colorToCss } from '@/lib/util'

interface Props {
  pxPerPt: number
  /** Esquina superior izquierda de la caja de texto, en puntos de página. */
  x: number
  y: number
  angle?: number
  style: TextStyle
  initial: string
  singleLine?: boolean
  /** Ancho mínimo en puntos (p. ej. el de la línea original que se edita). */
  minWidth?: number
  onCommit: (text: string) => void
  onCancel: () => void
}

/** Márgenes interiores con los que el motor compone el texto (ver sources.py). */
export function textPadding(size: number): { x: number; y: number } {
  return { x: 0.18 * size, y: 0.06 * size }
}

/**
 * Editor de texto en el sitio. Usa la misma fuente, tamaño e interlineado con
 * que el motor escribirá el texto en el PDF, para que al confirmar no «salte».
 */
export function TextEditor({ pxPerPt, x, y, angle = 0, style, initial, singleLine, minWidth, onCommit, onCancel }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const done = useRef(false)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    element.focus()
    if (initial) element.select()
  }, [initial])

  const finish = (commit: boolean): void => {
    if (done.current) return
    done.current = true
    const value = ref.current?.value ?? ''
    if (commit) onCommit(value)
    else onCancel()
  }

  const pad = textPadding(style.size)
  // Con ancho de columna (un párrafo), el ancho es fijo y las líneas se reparten solas, como en el PDF.
  const column = !singleLine && style.width ? style.width : undefined
  return (
    <textarea
      ref={ref}
      className={singleLine ? 'text-editor line' : column ? 'text-editor wrap' : 'text-editor'}
      defaultValue={initial}
      rows={1}
      spellCheck={false}
      style={{
        left: x * pxPerPt,
        top: y * pxPerPt,
        minWidth: minWidth ? minWidth * pxPerPt : undefined,
        width: column ? column * pxPerPt : undefined,
        padding: `${pad.y * pxPerPt}px ${pad.x * pxPerPt}px`,
        fontFamily: `"${style.font}", Arial, sans-serif`,
        fontSize: style.size * pxPerPt,
        lineHeight: style.lineHeight,
        fontWeight: style.bold ? 700 : 400,
        fontStyle: style.italic ? 'italic' : 'normal',
        color: colorToCss(style.color, '#000'),
        textAlign: style.align,
        transform: angle ? `rotate(${angle}deg)` : undefined
      }}
      onPointerDown={(event) => event.stopPropagation()}
      onBlur={() => finish(true)}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key === 'Escape') {
          event.preventDefault()
          finish(false)
        } else if (event.key === 'Enter' && (singleLine || event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          finish(true)
        }
      }}
    />
  )
}
