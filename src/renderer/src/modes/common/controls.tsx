import { Ban, ChevronDown, ChevronUp } from 'lucide-react'
import { type ReactNode, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Color } from '@shared/types'
import { L } from '@/i18n'
import { cn, colorToCss, colorToHex, hexToColor } from '@/lib/util'
import { PALETTE } from './toolState'

function same(a: Color | null, b: Color | null): boolean {
  if (!a || !b) return a === b
  return a.every((value, index) => Math.abs(value - b[index]) < 0.02)
}

/** Botón con una muestra de color que abre una paleta. */
export function ColorButton({
  value,
  onChange,
  palette = PALETTE,
  allowNone,
  tip,
  icon
}: {
  value: Color | null
  onChange: (color: Color | null) => void
  palette?: Color[]
  allowNone?: boolean
  tip?: string
  icon?: ReactNode
}) {
  const button = useRef<HTMLButtonElement>(null)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  return (
    <>
      <button
        ref={button}
        className={cn('swatch-btn', anchor && 'on')}
        data-tip={anchor ? undefined : tip}
        onClick={() => setAnchor(button.current?.getBoundingClientRect() ?? null)}
      >
        {icon}
        <span className={cn('swatch', !value && 'none')} style={value ? { background: colorToCss(value) } : undefined} />
      </button>
      {anchor &&
        createPortal(
          <>
            <div className="popover-catch" onPointerDown={() => setAnchor(null)} />
            <div
              className="popover"
              style={{ left: Math.min(anchor.left, window.innerWidth - 196), top: anchor.bottom + 6 }}
              onKeyDown={(event) => event.key === 'Escape' && setAnchor(null)}
            >
              <div className="swatches">
                {allowNone && (
                  <button
                    className={cn('swatch none', !value && 'on')}
                    title={L.modes.colorPicker.none}
                    onClick={() => {
                      onChange(null)
                      setAnchor(null)
                    }}
                  >
                    <Ban size={13} />
                  </button>
                )}
                {palette.map((color, index) => (
                  <button
                    key={index}
                    className={cn('swatch', same(color, value) && 'on')}
                    style={{ background: colorToCss(color) }}
                    onClick={() => {
                      onChange(color)
                      setAnchor(null)
                    }}
                  />
                ))}
              </div>
              <label className="custom-color">
                <input
                  type="color"
                  value={colorToHex(value)}
                  onChange={(event) => onChange(hexToColor(event.target.value))}
                />
                <span>{L.modes.colorPicker.custom}</span>
              </label>
            </div>
          </>,
          document.body
        )}
    </>
  )
}

/** Campo numérico compacto con flechas y validación de rango. */
export function NumberField({
  value,
  onChange,
  min,
  max,
  step = 1,
  width = 56,
  suffix,
  tip
}: {
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step?: number
  width?: number
  suffix?: string
  tip?: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = (raw: string): void => {
    const parsed = parseFloat(raw.replace(',', '.'))
    if (Number.isFinite(parsed)) onChange(Math.min(max, Math.max(min, parsed)))
    setDraft(null)
  }
  const nudge = (direction: 1 | -1): void => {
    setDraft(null)
    onChange(Math.min(max, Math.max(min, Math.round((value + direction * step) * 100) / 100)))
  }
  return (
    // Las flechas ocupan su sitio a la derecha: el ancho pedido es el del número.
    <label className="num" data-tip={tip} style={{ width: width + 16 }}>
      <input
        value={draft ?? String(Math.round(value * 100) / 100)}
        inputMode="decimal"
        aria-label={tip}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={(event) => commit(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault()
            nudge(event.key === 'ArrowUp' ? 1 : -1)
          }
        }}
      />
      {suffix && <span>{suffix}</span>}
      <span className="num-steps">
        <button type="button" tabIndex={-1} aria-label={L.modes.numberField.up} disabled={value >= max} onPointerDown={(event) => event.preventDefault()} onClick={() => nudge(1)}>
          <ChevronUp size={10} strokeWidth={2.5} />
        </button>
        <button type="button" tabIndex={-1} aria-label={L.modes.numberField.down} disabled={value <= min} onPointerDown={(event) => event.preventDefault()} onClick={() => nudge(-1)}>
          <ChevronDown size={10} strokeWidth={2.5} />
        </button>
      </span>
    </label>
  )
}

/** Contenedor de las opciones de la herramienta o del elemento seleccionado. */
export function SubBar({ children }: { children: ReactNode }) {
  return <div className="subbar">{children}</div>
}

export function SubLabel({ children }: { children: ReactNode }) {
  return <span className="sub-label">{children}</span>
}
