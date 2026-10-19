import { release } from 'node:os'
import { app } from 'electron'
import { EngineError } from './engine'
import { L } from './i18n'
import { log } from './log'

/**
 * Comentarios desde Folio (fallos, mejoras, lo que sea): llegan al autor por
 * correo a través de un script de Google suyo publicado como aplicación web.
 * Folio no puede mandar correo por sí solo sin llevar dentro una contraseña,
 * que cualquiera podría sacar (A04:2025): el servicio recibe el texto, aplica
 * su propio límite de envíos y lo reenvía al buzón de quien lo publicó.
 *
 * Solo viajan el texto del usuario, el tipo y las versiones de Folio y de
 * Windows: nada de documentos, rutas ni registros.
 */

/** Servicio de comentarios (Google Apps Script, termina en /exec). Vacío: los comentarios van a GitHub. */
export const FEEDBACK_URL =
  'https://script.google.com/macros/s/AKfycbzliNcJhE5CrGuaRSjLzbUCoPKqVfwHc4kYUlyaMEJWTycdszXlOnZgW5AoduYZxm-bYA/exec'

/** Únicos servidores a los que puede ir la petición, también tras una redirección (SSRF, A01:2025). */
const HOSTS = new Set(['script.google.com', 'script.googleusercontent.com'])
const TIMEOUT_MS = 15000
const MAX_REDIRECTS = 3
const MAX_REPLY_BYTES = 64 * 1024
/** Un envío cada tanto: que un doble clic o un bucle no llenen el buzón. */
const MIN_INTERVAL_MS = 15000

export type FeedbackKind = 'Fallo' | 'Mejora' | 'Otra cosa'

let lastSent = 0

/**
 * Solo pruebas, sin empaquetar: un servidor local hace de servicio, u «off»
 * lo quita (las pruebas nunca deben escribir al servicio de verdad).
 */
function testUrl(): string | null {
  return !app.isPackaged && process.env.FOLIO_TEST_FEEDBACK ? process.env.FOLIO_TEST_FEEDBACK : null
}

const serviceUrl = (): string => {
  const test = testUrl()
  return test === 'off' ? '' : (test ?? FEEDBACK_URL)
}

export const feedbackAvailable = (): boolean => serviceUrl() !== ''

const failed = (): EngineError =>
  new EngineError({ code: 'net_failed', message: L.main.feedback.failed })

function allowed(url: URL): boolean {
  if (url.username !== '' || url.password !== '') return false
  if (testUrl()) return url.protocol === 'http:' && url.hostname === '127.0.0.1'
  return url.protocol === 'https:' && HOSTS.has(url.hostname)
}

export async function sendFeedback(kind: FeedbackKind, message: string): Promise<void> {
  if (!feedbackAvailable()) throw new EngineError({ code: 'unsupported', message: L.main.feedback.viaGithub })
  if (Date.now() - lastSent < MIN_INTERVAL_MS) {
    throw new EngineError({ code: 'busy', message: L.main.feedback.wait })
  }
  const body = JSON.stringify({ app: 'folio', type: kind, message, version: app.getVersion(), system: `Windows ${release()}` })
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    // El servicio de Google responde con una redirección a la respuesta: se
    // sigue a mano para comprobar cada salto contra la lista de servidores.
    let url = new URL(serviceUrl())
    let init: RequestInit = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }
    for (let hop = 0; ; hop++) {
      if (!allowed(url)) throw failed()
      const response = await fetch(url, { ...init, redirect: 'manual', signal: controller.signal, credentials: 'omit' })
      if (response.status >= 300 && response.status < 400) {
        const next = response.headers.get('location')
        if (!next || hop >= MAX_REDIRECTS) throw failed()
        url = new URL(next, url)
        init = { method: 'GET' }
        continue
      }
      const length = Number(response.headers.get('content-length') ?? 0)
      if (!response.ok || length > MAX_REPLY_BYTES) {
        log.warn(`comentarios: el servicio respondió ${response.status}`)
        throw failed()
      }
      const text = await response.text()
      let reply: unknown = null
      try {
        reply = text.length <= MAX_REPLY_BYTES ? JSON.parse(text) : null
      } catch {
        reply = null
      }
      if (!reply || typeof reply !== 'object' || (reply as { ok?: unknown }).ok !== true) {
        log.warn('comentarios: el servicio no aceptó el envío')
        throw failed()
      }
      lastSent = Date.now()
      log.info('comentarios: enviado')
      return
    }
  } catch (error) {
    if (error instanceof EngineError) throw error
    // Solo el tipo de fallo: ni el texto del comentario ni la dirección (A09:2025).
    log.warn(`comentarios: no se pudo enviar (${error instanceof Error ? error.name : 'error'})`)
    throw failed()
  } finally {
    clearTimeout(timer)
  }
}
