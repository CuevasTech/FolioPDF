import { L, locale } from '@/i18n'

/** Une nombres de clase ignorando los valores vacíos. */
export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** Números con 0, 1 o 2 decimales fijos, con el separador del idioma de Folio. */
const decimals = [0, 1, 2].map((digits) => new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }))

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${decimals[bytes < 10240 ? 1 : 0].format(bytes / 1024)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${decimals[1].format(bytes / 1024 / 1024)} MB`
  return `${decimals[2].format(bytes / 1024 / 1024 / 1024)} GB`
}

const relative = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })

export function formatWhen(timestamp: number): string {
  const diff = timestamp - Date.now()
  const minutes = Math.round(diff / 60000)
  if (Math.abs(minutes) < 1) return L.state.when.now
  if (Math.abs(minutes) < 60) return relative.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return relative.format(hours, 'hour')
  const days = Math.round(hours / 24)
  if (Math.abs(days) < 30) return relative.format(days, 'day')
  return new Date(timestamp).toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Ejecuta `fn` como mucho una vez por fotograma. */
export function rafThrottle<A extends unknown[]>(fn: (...args: A) => void): ((...args: A) => void) & { cancel(): void } {
  let frame = 0
  let last: A | null = null
  const wrapped = (...args: A): void => {
    last = args
    if (frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      if (last) fn(...last)
    })
  }
  wrapped.cancel = () => {
    if (frame) cancelAnimationFrame(frame)
    frame = 0
  }
  return wrapped
}

export function colorToCss(color: readonly number[] | null | undefined, fallback = 'transparent'): string {
  if (!color || color.length < 3) return fallback
  const [r, g, b] = color.map((c) => Math.round(clamp(c, 0, 1) * 255))
  return `rgb(${r} ${g} ${b})`
}

export function hexToColor(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!m) return [0, 0, 0]
  return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255]
}

export function colorToHex(color: readonly number[] | null | undefined): string {
  if (!color || color.length < 3) return '#000000'
  return '#' + color.map((c) => Math.round(clamp(c, 0, 1) * 255).toString(16).padStart(2, '0')).join('')
}
