import { memo, useMemo } from 'react'
import type { Color, PageInfo, Rect } from '@shared/types'
import { fmt, L } from '@/i18n'
import { usePageData } from '@/lib/pageData'
import { cn } from '@/lib/util'
import { mutate } from '@/state/actions'
import { useApp } from '@/state/app'

export interface Widget {
  id: string
  name: string
  label: string
  type: 'text' | 'checkbox' | 'radio' | 'combo' | 'list' | 'button' | 'signature' | 'other'
  rect: Rect
  value: string | boolean | string[]
  options: string[]
  export: string | null
  group: string | null
  readOnly: boolean
  required: boolean
  multiline: boolean
  password: boolean
  editable: boolean
  multiSelect: boolean
  maxLength: number
  fontSize: number
  font: 'sans' | 'serif' | 'mono'
  align: 0 | 1 | 2
  textColor: Color
  fill: Color | null
  border: Color | null
  borderWidth: number
  signed: boolean
  /** El formulario lo calcula a partir de otros campos (un total): no se escribe. */
  calculated: boolean
  /** Qué admite el campo según su formato; null: cualquier texto. */
  format: 'number' | 'percent' | 'date' | null
  /** Cómo escribir la fecha («dd/mm/aaaa»). */
  hint: string | null
  /** Texto para corregir el valor (sin formato, con la coma del campo); null: el propio valor. */
  edit: string | null
}

/** Campos de la página en orden de lectura (arriba a abajo, izquierda a derecha). */
export function readingOrder(widgets: Widget[]): Widget[] {
  return [...widgets].sort((a, b) => Math.round(a.rect[1] / 6) - Math.round(b.rect[1] / 6) || a.rect[0] - b.rect[0])
}

const FONT_CSS = { sans: 'Helvetica, Arial, sans-serif', serif: '"Times New Roman", Times, serif', mono: '"Courier New", monospace' }
const ALIGN = ['left', 'center', 'right'] as const

interface Props {
  doc: string
  page: PageInfo
  pxPerPt: number
  onSign?: (widget: Widget) => void
}

/**
 * Rellenado de formularios: sobre cada campo se coloca un control nativo
 * transparente. El valor que se ve es el que dibuja el motor en la página; el
 * control solo se hace visible mientras se escribe.
 */
export const FillLayer = memo(function FillLayer({ doc, page, pxPerPt, onSign }: Props) {
  const data = usePageData<{ widgets: Widget[] }>(doc, page.id, page.gen, 'widgets')
  const tint = useApp((s) => s.settings.highlightFields)
  const widgets = useMemo(() => readingOrder((data?.widgets ?? []).filter((w) => !w.readOnly)), [data])
  if (widgets.length === 0) return null

  /** Devuelve false si el motor rechaza el valor (el aviso ya se ha mostrado). */
  const set = async (widget: Widget, value: string | boolean): Promise<boolean> => {
    if (value === (widget.edit ?? widget.value)) return true
    const done = await mutate(doc, 'form.setValue', { page: page.id, id: widget.id, value }, { priority: 9 })
    return done !== null
  }
  /** Guarda lo escrito; si no es válido (una fecha imposible, letras en un importe) vuelve el valor anterior. */
  const commit = (widget: Widget, control: HTMLInputElement | HTMLTextAreaElement, previous: string): void => {
    void set(widget, control.value).then((ok) => {
      if (!ok) control.value = previous
    })
  }

  return (
    <div className="over-layer">
      {widgets.map((w) => {
        const [x0, y0, x1, y1] = w.rect
        const box = { left: x0 * pxPerPt, top: y0 * pxPerPt, width: (x1 - x0) * pxPerPt, height: (y1 - y0) * pxPerPt }
        const title = w.label || w.name
        if (w.type === 'text' && w.calculated) {
          return (
            <div
              key={w.id}
              className={cn('field-calculated', tint && 'tint')}
              style={box}
              title={fmt(L.form.fill.calculatedTip, { title })}
              aria-label={fmt(L.form.fill.calculatedLabel, { title })}
              aria-readonly
            />
          )
        }
        if (w.type === 'text') {
          const auto = Math.min(12, Math.max(6, (y1 - y0) * 0.66))
          const style = {
            ...box,
            fontSize: (w.fontSize > 0 ? w.fontSize : w.multiline ? 10 : auto) * pxPerPt,
            fontFamily: FONT_CSS[w.font],
            textAlign: ALIGN[w.align]
          }
          const current = w.edit ?? String(w.value ?? '')
          const common = {
            className: cn('field-input', tint && 'tint', w.required && !w.value && 'required'),
            defaultValue: current,
            title: w.hint ? `${title} · ${w.hint}` : title,
            placeholder: w.hint ?? undefined,
            inputMode: w.format === 'number' || w.format === 'percent' ? ('decimal' as const) : undefined,
            maxLength: w.maxLength > 0 ? w.maxLength : undefined,
            spellCheck: false,
            'aria-label': title,
            'aria-required': w.required
          }
          // La clave incluye el valor: si cambia por otra vía (deshacer), el control se rehace.
          const key = `${w.id}:${String(w.value)}`
          if (w.multiline) {
            return (
              <textarea
                key={key}
                {...common}
                style={{ ...style, padding: `${2 * pxPerPt}px ${2 * pxPerPt}px`, lineHeight: 1.2 }}
                onBlur={(event) => commit(w, event.currentTarget, current)}
                onKeyDown={(event) => event.stopPropagation()}
              />
            )
          }
          return (
            <input
              key={key}
              {...common}
              type={w.password ? 'password' : 'text'}
              autoComplete="off"
              style={{ ...style, padding: `0 ${2 * pxPerPt}px` }}
              onBlur={(event) => commit(w, event.currentTarget, current)}
              onKeyDown={(event) => {
                event.stopPropagation()
                if (event.key === 'Enter') event.currentTarget.blur()
                else if (event.key === 'Escape') {
                  event.currentTarget.value = current
                  event.currentTarget.blur()
                }
              }}
            />
          )
        }
        if (w.type === 'checkbox' || w.type === 'radio') {
          const checked = w.value === true
          return (
            <div
              key={w.id}
              className={cn('field-toggle', w.type, tint && 'tint')}
              style={box}
              role={w.type}
              aria-checked={checked}
              aria-label={title}
              title={title}
              tabIndex={0}
              onClick={() => void set(w, w.type === 'radio' ? true : !checked)}
              onKeyDown={(event) => {
                if (event.key === ' ' || event.key === 'Enter') {
                  event.preventDefault()
                  event.stopPropagation()
                  void set(w, w.type === 'radio' ? true : !checked)
                }
              }}
            />
          )
        }
        if (w.type === 'combo' || w.type === 'list') {
          const current = Array.isArray(w.value) ? (w.value[0] ?? '') : String(w.value ?? '')
          return (
            <select
              key={`${w.id}:${current}`}
              className={cn('field-input', tint && 'tint')}
              style={{ ...box, fontSize: Math.min(12, (y1 - y0) * 0.6) * pxPerPt }}
              defaultValue={current}
              title={title}
              aria-label={title}
              onChange={(event) => void set(w, event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {!w.options.includes(current) && <option value={current}>{current}</option>}
              {w.options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          )
        }
        if (w.type === 'signature' && !w.signed && onSign) {
          return (
            <div
              key={w.id}
              className={cn('field-toggle', tint && 'tint')}
              style={box}
              role="button"
              tabIndex={0}
              title={L.form.fill.signHere}
              onClick={() => onSign(w)}
            />
          )
        }
        return null
      })}
    </div>
  )
})
