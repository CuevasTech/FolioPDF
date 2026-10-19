import { Bold, Italic, TextAlignCenter, TextAlignEnd, TextAlignJustify, TextAlignStart } from 'lucide-react'
import type { TextStyle } from '@shared/types'
import { L } from '@/i18n'
import { cn } from '@/lib/util'
import { useApp } from '@/state/app'
import { ColorButton, NumberField } from './controls'

/** Controles de fuente, tamaño, estilo, color y alineación de un texto. */
export function TextStyleControls({
  value,
  onChange,
  align = true
}: {
  value: TextStyle
  onChange: (patch: Partial<TextStyle>) => void
  align?: boolean
}) {
  const fonts = useApp((s) => s.fonts)
  const family = fonts.find((f) => f.family === value.font)
  return (
    <>
      <select
        className="select sm"
        value={value.font}
        aria-label={L.modes.textStyle.font}
        style={{ fontFamily: `"${value.font}", sans-serif` }}
        onChange={(event) => onChange({ font: event.target.value })}
      >
        {fonts.map((font) => (
          <option key={font.family} value={font.family} style={{ fontFamily: `"${font.family}", sans-serif` }}>
            {font.family}
          </option>
        ))}
      </select>
      <NumberField value={value.size} min={4} max={400} onChange={(size) => onChange({ size })} tip={L.modes.textStyle.size} suffix="pt" width={64} />
      <button
        className={cn('icon-btn', value.bold && 'on')}
        data-tip={L.modes.textStyle.bold}
        disabled={family ? !family.bold : false}
        onClick={() => onChange({ bold: !value.bold })}
      >
        <Bold size={15} />
      </button>
      <button
        className={cn('icon-btn', value.italic && 'on')}
        data-tip={L.modes.textStyle.italic}
        disabled={family ? !family.italic : false}
        onClick={() => onChange({ italic: !value.italic })}
      >
        <Italic size={15} />
      </button>
      <ColorButton value={value.color} onChange={(color) => color && onChange({ color })} tip={L.modes.textStyle.color} />
      {align && (
        <>
          <div className="sep" />
          {(
            [
              ['left', TextAlignStart, L.modes.textStyle.alignLeft],
              ['center', TextAlignCenter, L.modes.textStyle.alignCenter],
              ['right', TextAlignEnd, L.modes.textStyle.alignRight],
              // Justificar solo tiene sentido en un texto con ancho de columna (un párrafo).
              ...(value.width ? ([['justify', TextAlignJustify, L.modes.textStyle.justify]] as const) : [])
            ] as const
          ).map(([key, Icon, tip]) => (
            <button key={key} className={cn('icon-btn', value.align === key && 'on')} data-tip={tip} onClick={() => onChange({ align: key })}>
              <Icon size={15} />
            </button>
          ))}
        </>
      )}
    </>
  )
}
