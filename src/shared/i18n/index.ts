import { en } from './en/index'
import { es } from './es/index'

/**
 * Idiomas de Folio. El español es el original y define qué textos hay: el
 * inglés tiene que tener exactamente las mismas claves, y TypeScript no compila
 * si falta o sobra alguna.
 *
 * El idioma se fija al abrir Folio y no cambia mientras está abierto.
 */
export type Lang = 'es' | 'en'
export type LangSetting = 'auto' | Lang
export type Messages = typeof es
/** Un texto con singular y plural; {n} es la cantidad. */
export type Plural = { one: string; other: string }

const CATALOG: Record<Lang, Messages> = { es, en }

export function messagesFor(lang: Lang): Messages {
  return CATALOG[lang]
}

/** Idiomas de España con los que el español se entiende mejor que el inglés. */
const SPANISH_FAMILY = new Set(['es', 'ca', 'gl', 'eu'])

const baseOf = (tag: string): string => tag.toLowerCase().split(/[-_]/)[0] ?? ''

/**
 * El idioma de Folio: el elegido en Ajustes o, en automático, el primero de los
 * idiomas de Windows que Folio tenga. Si no tiene ninguno, inglés.
 */
export function resolveLang(setting: LangSetting, preferred: readonly string[]): Lang {
  if (setting !== 'auto') return setting
  for (const tag of preferred) {
    const base = baseOf(tag)
    if (SPANISH_FAMILY.has(base)) return 'es'
    if (base === 'en') return 'en'
  }
  return 'en'
}

const FALLBACK_LOCALE: Record<Lang, string> = { es: 'es-ES', en: 'en-US' }

/**
 * Formato de fechas y números: la región de Windows si es del mismo idioma
 * (es-MX, en-GB...). La etiqueta se valida con Intl antes de usarla.
 */
export function localeFor(lang: Lang, preferred: readonly string[]): string {
  for (const tag of preferred) {
    if (baseOf(tag) !== lang) continue
    try {
      const [canonical] = Intl.getCanonicalLocales(tag)
      if (canonical) return canonical
    } catch {
      // etiqueta no válida: se prueba la siguiente
    }
  }
  return FALLBACK_LOCALE[lang]
}

/** Etiqueta de idioma con forma válida (para lo que llega como argumento). */
export const LOCALE_RE = /^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$/

/** Rellena los {nombre} del texto; un nombre sin valor se deja tal cual. */
export function fmt(template: string, values: Record<string, string | number> = {}): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole
  )
}

/** Singular o plural según la cantidad, con las reglas del idioma. */
export function pluralOf(lang: Lang): (forms: Plural, n: number, values?: Record<string, string | number>) => string {
  const rules = new Intl.PluralRules(lang)
  return (forms, n, values = {}) => fmt(rules.select(n) === 'one' ? forms.one : forms.other, { n, ...values })
}
