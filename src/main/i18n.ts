import { app } from 'electron'
import { fmt, type Lang, type LangSetting, localeFor, messagesFor, pluralOf, resolveLang } from '@shared/i18n'

/**
 * Idioma de esta sesión para el proceso principal (diálogos de Windows,
 * avisos), la interfaz y el motor. Se decide una vez al abrir Folio, con el
 * ajuste guardado y los idiomas de Windows.
 */
export let lang: Lang = 'es'
export let locale = 'es-ES'
export let L = messagesFor('es')
export let plural = pluralOf('es')
export { fmt }

/** Solo en pruebas (sin empaquetar): FOLIO_TEST_LANG fija el idioma sin depender del de Windows. */
function testLang(): Lang | null {
  const value = !app.isPackaged ? process.env.FOLIO_TEST_LANG : undefined
  return value === 'es' || value === 'en' ? value : null
}

export function initLang(setting: LangSetting): void {
  const preferred = app.getPreferredSystemLanguages()
  lang = testLang() ?? resolveLang(setting, preferred)
  locale = localeFor(lang, preferred)
  L = messagesFor(lang)
  plural = pluralOf(lang)
}
