import type { DateStyle } from '@shared/types'
import { L, locale } from '@/i18n'

/** Formatos de la herramienta «Fecha de hoy» (al firmar), en el orden en que se ofrecen. */
export const DATE_STYLES: { value: DateStyle; label: string }[] = [
  { value: 'short', label: L.state.dateStyles.short },
  { value: 'shortTime', label: L.state.dateStyles.shortTime },
  { value: 'medium', label: L.state.dateStyles.medium },
  { value: 'long', label: L.state.dateStyles.long },
  { value: 'full', label: L.state.dateStyles.full },
  { value: 'iso', label: L.state.dateStyles.iso }
]

const pad = (n: number): string => String(n).padStart(2, '0')

/**
 * La fecha (de hoy, si no se indica otra) escrita en el formato elegido, con
 * las convenciones del idioma y la región de Folio (en España, «14/10/2026»).
 */
export function formatDateStyle(style: DateStyle, when: Date = new Date()): string {
  switch (style) {
    case 'shortTime':
      return `${formatDateStyle('short', when)} ${when.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })}`
    case 'medium':
      // «14 oct 2026» (sin el punto de la abreviatura, que en un documento sobra)
      return when.toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' }).replace('.', '')
    case 'long':
      return when.toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' })
    case 'full':
      return when.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
    case 'iso':
      return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
    default:
      return when.toLocaleDateString(locale, { day: '2-digit', month: '2-digit', year: 'numeric' })
  }
}
