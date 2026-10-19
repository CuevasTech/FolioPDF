import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { app, type Session, session } from 'electron'
import { autoUpdater } from 'electron-updater'
import type { UpdateStatus } from '@shared/types'
import { L } from './i18n'
import { log } from './log'

/**
 * Versiones nuevas de Folio, publicadas en GitHub Releases.
 *
 * Al abrir Folio (si el usuario no lo ha desactivado) se mira si hay una versión
 * nueva y se avisa. No se descarga nada hasta que el usuario pulsa «Actualizar»
 * ni se instala sin su permiso: con «Reiniciar y actualizar» o, ya descargada,
 * al cerrar Folio.
 *
 * Seguridad de la cadena de suministro (OWASP A03:2025 / A08:2025):
 * - Solo se habla con GitHub y por HTTPS: la sesión de red que usa
 *   electron-updater corta cualquier otra petición, también tras una redirección.
 * - El instalador descargado se comprueba con la huella SHA-512 publicada en
 *   latest.yml; si no coincide, se descarta.
 * - Con el instalador firmado (publisherName en electron-builder.yml), además
 *   se exige que la versión nueva venga firmada por el mismo editor.
 */

// Partición de red que usa electron-updater (electronHttpExecutor.NET_SESSION_NAME).
const NET_SESSION = 'electron-updater'
const VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,6}(-[0-9A-Za-z.-]{1,30})?$/

/** Peticiones de actualización: solo HTTPS a GitHub (y, sin empaquetar, la dirección de prueba). */
export function isUpdateUrl(raw: string, testOrigin: string | null): boolean {
  try {
    const url = new URL(raw)
    if (testOrigin !== null && url.origin === testOrigin) return true
    if (url.protocol !== 'https:' || url.username !== '' || url.password !== '') return false
    return url.hostname === 'github.com' || url.hostname === 'api.github.com' || url.hostname.endsWith('.githubusercontent.com')
  } catch {
    return false
  }
}

function hardenUpdateSession(target: Session, testOrigin: string | null): void {
  target.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  target.setPermissionCheckHandler(() => false)
  target.webRequest.onBeforeRequest((details, callback) => {
    const allowed = isUpdateUrl(details.url, testOrigin)
    if (!allowed) log.warn('actualizaciones: se bloquea una petición fuera de GitHub')
    callback({ cancel: !allowed })
  })
}

/**
 * Dirección de prueba (solo sin empaquetar): un servidor local con un latest.yml
 * de prueba, para las pruebas de interfaz. En la aplicación instalada no existe.
 */
function testFeed(raw: string | undefined): string | null {
  if (app.isPackaged || !raw) return null
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' ? url.origin : null
  } catch {
    return null
  }
}

/** Versión anunciada por el servidor: se muestra en la interfaz, así que se valida. */
function cleanVersion(value: unknown): string {
  return typeof value === 'string' && VERSION.test(value) ? value : '?'
}

export class Updates {
  private status: UpdateStatus
  private readonly active: boolean
  private manual = false
  private installing = false

  constructor(private readonly notify: (status: UpdateStatus) => void) {
    const feed = testFeed(process.env.FOLIO_TEST_UPDATES)
    this.active = app.isPackaged || feed !== null
    this.status = { state: this.active ? 'idle' : 'unavailable' }
    if (!this.active) return

    hardenUpdateSession(session.fromPartition(NET_SESSION, { cache: false }), feed)
    autoUpdater.logger = {
      // electron-updater registra direcciones y versiones, nada del usuario.
      info: (message: unknown) => log.info(`actualizaciones: ${String(message).slice(0, 300)}`),
      warn: (message: unknown) => log.warn(`actualizaciones: ${String(message).slice(0, 300)}`),
      error: (message: unknown) => log.warn(`actualizaciones: ${String(message).slice(0, 300)}`),
      debug: () => undefined
    }
    autoUpdater.autoDownload = false // se pregunta antes de descargar
    autoUpdater.autoInstallOnAppQuit = false // se instala solo con permiso (ver install)
    autoUpdater.allowPrerelease = false
    autoUpdater.allowDowngrade = false
    autoUpdater.disableWebInstaller = true
    if (feed !== null) {
      // Solo pruebas: configuración en el perfil (temporal) de la prueba y una
      // carpeta de descargas propia, para no mezclarse con una instalación real.
      const config = join(app.getPath('userData'), 'prueba-actualizaciones.yml')
      writeFileSync(config, `provider: generic\nurl: ${feed}\nupdaterCacheDirName: folio-updater-pruebas\n`)
      autoUpdater.forceDevUpdateConfig = true
      autoUpdater.updateConfigPath = config
      autoUpdater.setFeedURL({ provider: 'generic', url: feed })
    }

    autoUpdater.on('checking-for-update', () => this.set({ state: 'checking' }))
    autoUpdater.on('update-available', (info) => this.set({ state: 'available', version: cleanVersion(info.version) }))
    autoUpdater.on('update-not-available', () => this.set({ state: this.manual ? 'current' : 'idle' }))
    autoUpdater.on('download-progress', (progress) =>
      this.set({ state: 'downloading', version: this.status.version, percent: Math.max(0, Math.min(100, Math.floor(progress.percent))) })
    )
    autoUpdater.on('update-downloaded', (info) => this.set({ state: 'ready', version: cleanVersion(info.version) }))
    autoUpdater.on('error', (error: Error) => {
      // Solo el tipo y un mensaje propio: el de la biblioteca puede incluir rutas.
      log.warn(`actualizaciones: error (${error?.name ?? 'desconocido'})`)
      const wasDownloading = this.status.state === 'downloading'
      if (!this.manual && !wasDownloading) {
        // Una comprobación automática que falla (sin conexión, por ejemplo) no molesta.
        this.set({ state: 'idle' })
        return
      }
      const mismatch = /sha512|checksum/i.test(String(error?.message ?? ''))
      this.set({
        state: 'error',
        version: this.status.version,
        message: mismatch
          ? L.main.updates.mismatch
          : wasDownloading
            ? L.main.updates.downloadFailed
            : L.main.updates.checkFailed
      })
    })
  }

  private set(status: UpdateStatus): void {
    this.status = status
    this.notify(status)
  }

  current(): UpdateStatus {
    return this.status
  }

  /** Mira si hay versión nueva. ``manual``: lo ha pedido el usuario (se le dice también si no la hay). */
  check(manual: boolean): void {
    if (!this.active || this.installing || ['checking', 'downloading', 'ready'].includes(this.status.state)) return
    this.manual = manual
    autoUpdater.checkForUpdates().catch(() => undefined) // el fallo llega por el evento 'error'
  }

  /** Descarga la versión anunciada: solo después de que el usuario lo pida. */
  download(): void {
    if (!this.active || (this.status.state !== 'available' && this.status.state !== 'error')) return
    this.set({ state: 'downloading', version: this.status.version, percent: 0 })
    autoUpdater.downloadUpdate().catch(() => undefined)
  }

  /** Hay una versión descargada esperando a instalarse. */
  get pending(): boolean {
    return this.status.state === 'ready' && !this.installing
  }

  /**
   * Instala la versión descargada. La interfaz ya ha cerrado los documentos
   * (preguntando qué guardar). ``relaunch``: abrir Folio de nuevo al terminar.
   */
  install(relaunch: boolean): boolean {
    if (!this.pending) return false
    this.installing = true
    log.info('actualizaciones: se instala la versión descargada')
    autoUpdater.quitAndInstall(true, relaunch)
    return true
  }
}
