import { readFile } from 'node:fs/promises'
import { extname, join, normalize, sep } from 'node:path'
import { app, protocol, type IpcMainInvokeEvent, type Session, type WebContents } from 'electron'

/**
 * Endurecimiento de la capa web de la aplicación.
 *
 * La interfaz es código propio, pero muestra datos sacados de PDF que pueden
 * venir de cualquier parte. Por eso se trata como si pudiera verse comprometida:
 * corre aislada (sandbox, sin Node), no puede navegar, abrir ventanas, pedir
 * permisos ni hacer peticiones de red, y solo carga recursos de la propia app.
 */

export const APP_SCHEME = 'folio'
export const APP_ORIGIN = `${APP_SCHEME}://app`

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf'
}

/**
 * Política de contenido de la interfaz empaquetada:
 * - sin scripts en línea ni `eval` (script-src 'self');
 * - sin conexiones salientes (connect-src 'none');
 * - los estilos en línea solo se permiten como atributo (los usa React para
 *   posiciones y tamaños), no como bloques <style>.
 */
const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "style-src-attr 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'none'",
  "worker-src 'self' blob:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
  "object-src 'none'"
].join('; ')

/** Debe llamarse antes de que la aplicación esté lista. */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } }
  ])
}

/**
 * Sirve los archivos de la interfaz desde `folio://app/` en lugar de `file://`,
 * y delega en `printPages` las hojas de impresión (`folio://print/…`).
 */
export function serveApp(
  rendererDir: string,
  printPages: (url: URL) => Promise<Response>,
  extraFont: (style: string) => Promise<Buffer | null> = async () => null
): void {
  const base = normalize(rendererDir)
  protocol.handle(APP_SCHEME, async (request) => {
    try {
      const url = new URL(request.url)
      if (request.method !== 'GET') return new Response(null, { status: 405 })
      if (url.host === 'print') return await printPages(url)
      if (url.host !== 'app') return new Response(null, { status: 404 })
      // Fuentes extra (Aptos, ver fonts.ts): nombres fijos, nunca una ruta recibida.
      const font = /^\/fonts\/aptos-(regular|bold|italic|boldItalic)\.ttf$/.exec(url.pathname)
      if (font) {
        const data = await extraFont(font[1])
        if (!data) return new Response(null, { status: 404 })
        return new Response(new Uint8Array(data), {
          status: 200,
          headers: { 'Content-Type': 'font/ttf', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache' }
        })
      }
      let relative = decodeURIComponent(url.pathname)
      if (relative === '/' || relative === '') relative = '/index.html'
      const file = normalize(join(base, relative))
      // La ruta resuelta debe seguir dentro de la carpeta de la interfaz: así
      // un "../" en la URL no puede leer otros archivos del disco (A01:2025).
      if (!file.startsWith(base + sep)) return new Response(null, { status: 403 })
      const type = MIME[extname(file).toLowerCase()]
      if (!type) return new Response(null, { status: 404 })
      const data = await readFile(file)
      return new Response(new Uint8Array(data), {
        status: 200,
        headers: {
          'Content-Type': type,
          'Content-Security-Policy': CSP,
          'X-Content-Type-Options': 'nosniff',
          'Referrer-Policy': 'no-referrer',
          'Cache-Control': 'no-cache'
        }
      })
    } catch {
      return new Response(null, { status: 404 })
    }
  })
}

function devOrigin(): string | null {
  const raw = app.isPackaged ? undefined : process.env.ELECTRON_RENDERER_URL
  if (!raw) return null
  try {
    return new URL(raw).origin
  } catch {
    return null
  }
}

export function isAppUrl(raw: string): boolean {
  try {
    const url = new URL(raw)
    if (url.protocol === `${APP_SCHEME}:` && (url.host === 'app' || url.host === 'print')) return true
    const dev = devOrigin()
    return dev !== null && url.origin === dev
  } catch {
    return false
  }
}

export function hardenSession(session: Session): void {
  // Ningún permiso del navegador (cámara, micrófono, ubicación, notificaciones…).
  session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  session.setPermissionCheckHandler(() => false)

  // La interfaz no necesita red para nada. Se corta cualquier petición que no
  // sea a la propia aplicación: aunque se colara código ajeno, no podría enviar
  // fuera el contenido de un documento (A01:2025).
  const dev = devOrigin()
  const devHost = dev ? new URL(dev).host : null
  session.webRequest.onBeforeRequest((details, callback) => {
    let allowed = false
    try {
      const url = new URL(details.url)
      allowed =
        (url.protocol === `${APP_SCHEME}:` && (url.host === 'app' || url.host === 'print')) ||
        url.protocol === 'data:' ||
        url.protocol === 'blob:' ||
        url.protocol === 'devtools:' ||
        (devHost !== null && url.host === devHost && ['http:', 'ws:'].includes(url.protocol))
    } catch {
      allowed = false
    }
    callback({ cancel: !allowed })
  })
}

export function hardenContents(): void {
  app.on('web-contents-created', (_event, contents: WebContents) => {
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
    contents.on('will-navigate', (event, url) => {
      if (!isAppUrl(url)) event.preventDefault()
    })
    contents.on('will-redirect', (event, url) => {
      if (!isAppUrl(url)) event.preventDefault()
    })
    contents.on('will-attach-webview', (event) => event.preventDefault())
  })
}

/**
 * Solo se atienden mensajes que vengan del marco principal de nuestra ventana
 * y cargado desde la propia aplicación (A01:2025).
 */
export function isTrustedSender(event: IpcMainInvokeEvent | Electron.IpcMainEvent, contents: WebContents | null): boolean {
  const frame = event.senderFrame
  if (!contents || !frame || event.sender !== contents) return false
  if (frame !== contents.mainFrame) return false
  return isAppUrl(frame.url)
}
