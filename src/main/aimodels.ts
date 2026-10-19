import { createHash } from 'node:crypto'
import { createReadStream, createWriteStream, existsSync } from 'node:fs'
import { mkdir, readFile, rename, rm, stat, statfs, writeFile } from 'node:fs/promises'
import { totalmem } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { app, net, type Session, session } from 'electron'
import { z } from 'zod'
import type { AiDownloadError, AiDownloadProgress, AiStatus, AiTier } from '@shared/ai'
import { CATALOG, type AiComponent, type AiFile } from './aicatalog'
import { log } from './log'

/**
 * Modelos de IA en este equipo: descarga, comprobación, actualización y borrado.
 *
 * Seguridad de la cadena de suministro (OWASP A03:2025 y LLM03:2025):
 * - Solo se descarga lo que está en el catálogo que va DENTRO de esta versión
 *   de Folio (aicatalog.ts): dirección fija a una revisión concreta, tamaño
 *   exacto y huella SHA-256. Nada viene de un catálogo remoto.
 * - Solo HTTPS y solo a los servidores de Hugging Face, también tras cada
 *   redirección (se comprueban una a una, sin seguirlas a ciegas).
 * - Un archivo no pasa a su sitio hasta que su tamaño y su huella coinciden;
 *   si no, se borra. Nunca se descarga código, solo pesos de modelos (datos).
 * - La carpeta de modelos es del usuario; el motor aislado solo puede leerla.
 *
 * Los modelos se actualizan con Folio: si una versión nueva trae otro modelo
 * en el catálogo, se ofrece descargarlo y, ya comprobado, sustituye al
 * anterior, que se borra.
 */

const MAX_REDIRECTS = 5
// Sesión de red propia de las descargas de modelos (en memoria): la principal de
// Folio lo bloquea todo (security.ts). Esta solo deja pasar los servidores de
// Hugging Face, también en cada redirección (defensa en profundidad).
const MODELS_PARTITION = 'folio-models'
let modelsSession: Session | null = null

function downloadSession(): Session {
  if (modelsSession) return modelsSession
  const own = session.fromPartition(MODELS_PARTITION)
  own.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  own.setPermissionCheckHandler(() => false)
  own.webRequest.onBeforeRequest((details, callback) => {
    const allowed = isModelHost(details.url)
    if (!allowed) log.warn('ia: se bloquea una petición fuera de Hugging Face')
    callback({ cancel: !allowed })
  })
  modelsSession = own
  return own
}
const DISK_MARGIN = 512 * 1024 ** 2 // se deja libre al menos esto además de la descarga

/** Servidores de Hugging Face y su red de distribución (sin otros orígenes). */
export function isModelHost(raw: string): boolean {
  try {
    const url = new URL(raw)
    // Solo en pruebas: el servidor local del catálogo de prueba.
    if (CATALOG.testOrigin !== null && url.origin === CATALOG.testOrigin) return true
    if (url.protocol !== 'https:' || url.username !== '' || url.password !== '' || url.port !== '') return false
    const host = url.hostname.toLowerCase()
    return host === 'huggingface.co' || host.endsWith('.huggingface.co') || host === 'hf.co' || host.endsWith('.hf.co')
  } catch {
    return false
  }
}

const InstalledFileSchema = z.object({
  name: z.string().regex(/^[\w.-]{1,120}$/),
  size: z.number().int().positive(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/)
})
const InstalledSchema = z.object({
  components: z
    .record(
      z.string().regex(/^[a-z0-9-]{1,40}$/),
      z.object({ version: z.string().regex(/^[\w.-]{1,40}$/), files: z.array(InstalledFileSchema).max(20) })
    )
    .default({})
})
type Installed = z.infer<typeof InstalledSchema>

export class AiModels {
  readonly root: string
  private installed: Installed = { components: {} }
  private abort: (() => void) | null = null
  private busy = false

  constructor(
    userData: string,
    private readonly onProgress: (progress: AiDownloadProgress | null) => void
  ) {
    // Solo en pruebas (sin empaquetar): una carpeta de modelos ya preparada.
    const testRoot = !app.isPackaged ? process.env.FOLIO_TEST_AI_ROOT : undefined
    this.root = testRoot && isAbsolute(testRoot) ? testRoot : join(userData, 'models')
  }

  private manifestPath(): string {
    return join(this.root, 'installed.json')
  }

  /** Carpeta de una versión de un componente (nombres del catálogo: sin rutas ni «..»). */
  componentDir(component: AiComponent): string {
    return this.inside(join(this.root, component.id, component.version))
  }

  /** Toda ruta que se toca está dentro de la carpeta de modelos (A01:2025). */
  private inside(path: string): string {
    const full = resolve(path)
    const rel = relative(resolve(this.root), full)
    if (rel === '' || rel.startsWith('..') || rel.includes(`..${sep}`)) throw new Error('ruta fuera de la carpeta de modelos')
    return full
  }

  async load(): Promise<void> {
    try {
      const raw = JSON.parse(await readFile(this.manifestPath(), 'utf8')) as unknown
      const parsed = InstalledSchema.safeParse(raw)
      this.installed = parsed.success ? parsed.data : { components: {} }
      if (!parsed.success) log.warn('ia: lista de modelos instalados no válida; se ignora')
    } catch {
      this.installed = { components: {} }
    }
  }

  private async saveManifest(): Promise<void> {
    await mkdir(this.root, { recursive: true })
    const temp = join(this.root, 'installed.json.tmp')
    await writeFile(temp, JSON.stringify(this.installed, null, 2), 'utf8')
    await rename(temp, this.manifestPath())
  }

  /** ¿Está instalada, completa y en su versión actual? (tamaños comprobados; la huella se comprobó al descargar). */
  async isCurrent(component: AiComponent): Promise<boolean> {
    const entry = this.installed.components[component.id]
    if (!entry || entry.version !== component.version) return false
    for (const file of component.files) {
      try {
        const info = await stat(join(this.componentDir(component), file.name))
        if (!info.isFile() || info.size !== file.size) return false
      } catch {
        return false
      }
    }
    return true
  }

  /** Componente instalado de un tipo para un nivel (para el motor): carpeta y archivos. */
  async ready(tier: AiTier, kind: AiComponent['kind']): Promise<{ dir: string; component: AiComponent } | null> {
    if (tier === 'off') return null
    const component = CATALOG.tiers[tier].map((id) => CATALOG.components[id]).find((c) => c.kind === kind)
    if (!component || !(await this.isCurrent(component))) return null
    return { dir: this.componentDir(component), component }
  }

  async status(tier: AiTier): Promise<AiStatus> {
    const wanted = tier === 'off' ? [] : CATALOG.tiers[tier].map((id) => CATALOG.components[id])
    const components = await Promise.all(
      wanted.map(async (c) => {
        const current = await this.isCurrent(c)
        const old = this.installed.components[c.id]
        return {
          id: c.id,
          kind: c.kind,
          size: c.files.reduce((sum, f) => sum + f.size, 0),
          license: c.license,
          licenseUrl: c.licenseUrl,
          installed: current,
          update: !current && old !== undefined && old.version !== c.version
        }
      })
    )
    let freeDisk = 0
    try {
      // Sin crear la carpeta solo por mirar: si aún no existe, el disco de la de arriba.
      const fsInfo = await statfs(existsSync(this.root) ? this.root : dirname(this.root))
      freeDisk = fsInfo.bavail * fsInfo.bsize
    } catch {
      freeDisk = 0
    }
    return {
      tier,
      components,
      ready: components.length > 0 && components.every((c) => c.installed),
      downloading: this.busy,
      ramGb: Math.round((totalmem() / 1024 ** 3) * 10) / 10,
      freeDisk,
      tierSizes: { basic: tierSize('basic'), advanced: tierSize('advanced') }
    }
  }

  /** Descarga lo que falte de un nivel. Si ya se está descargando, no hace nada. */
  async install(tier: Exclude<AiTier, 'off'>): Promise<void> {
    if (this.busy) return
    this.busy = true
    try {
      const pending: AiComponent[] = []
      for (const id of CATALOG.tiers[tier]) {
        const component = CATALOG.components[id]
        if (!(await this.isCurrent(component))) pending.push(component)
      }
      const total = pending.reduce((sum, c) => sum + c.files.reduce((s, f) => s + f.size, 0), 0)
      await mkdir(this.root, { recursive: true })
      const fsInfo = await statfs(this.root)
      if (fsInfo.bavail * fsInfo.bsize < total + DISK_MARGIN) throw new AiError('disk')
      let done = 0
      for (const component of pending) {
        const dir = this.componentDir(component)
        await mkdir(dir, { recursive: true })
        for (const file of component.files) {
          const target = join(dir, file.name)
          if (await this.matches(target, file)) {
            done += file.size
            continue
          }
          await this.download(file, target, (bytes) => this.onProgress({ done: done + bytes, total, component: component.id }))
          done += file.size
        }
        const previous = this.installed.components[component.id]
        this.installed.components[component.id] = {
          version: component.version,
          files: component.files.map((f) => ({ name: f.name, size: f.size, sha256: f.sha256 }))
        }
        await this.saveManifest()
        // La versión anterior ya no hace falta: se borra solo cuando la nueva está comprobada.
        if (previous && previous.version !== component.version) {
          await rm(this.inside(join(this.root, component.id, previous.version)), { recursive: true, force: true })
        }
      }
      await this.cleanup()
    } finally {
      this.busy = false
      this.abort = null
      this.onProgress(null)
    }
  }

  cancel(): void {
    this.abort?.()
  }

  async removeAll(): Promise<void> {
    if (this.busy) this.cancel()
    this.installed = { components: {} }
    if (existsSync(this.root)) await rm(this.root, { recursive: true, force: true })
  }

  /** Quita carpetas de componentes o versiones que no figuran en la lista de instalados. */
  private async cleanup(): Promise<void> {
    const keep = new Set(Object.entries(this.installed.components).map(([id, c]) => `${id}${sep}${c.version}`))
    const { readdir } = await import('node:fs/promises')
    for (const id of await readdir(this.root).catch(() => [] as string[])) {
      if (id === 'installed.json' || id === '.partial') continue
      if (!/^[a-z0-9-]{1,40}$/.test(id)) continue
      const versions = await readdir(join(this.root, id)).catch(() => [] as string[])
      for (const version of versions) {
        if (!keep.has(`${id}${sep}${version}`)) await rm(this.inside(join(this.root, id, version)), { recursive: true, force: true })
      }
    }
  }

  /** ¿El archivo que hay ya es el bueno? (tamaño y huella). */
  private async matches(path: string, file: AiFile): Promise<boolean> {
    try {
      const info = await stat(path)
      if (!info.isFile() || info.size !== file.size) return false
      return (await sha256Of(path)) === file.sha256
    } catch {
      return false
    }
  }

  /**
   * Descarga un archivo a «.partial», comprobando tamaño y huella mientras
   * llega, y solo entonces lo mueve a su sitio. Si se corta, la próxima vez
   * sigue por donde iba (y vuelve a comprobar lo ya bajado).
   */
  private async download(file: AiFile, target: string, progress: (bytes: number) => void): Promise<void> {
    const partialDir = this.inside(join(this.root, '.partial'))
    await mkdir(partialDir, { recursive: true })
    const part = join(partialDir, `${file.sha256}.part`)
    let hash = createHash('sha256')
    let have = 0
    try {
      const info = await stat(part)
      if (info.isFile() && info.size < file.size) {
        await new Promise<void>((done, fail) => {
          createReadStream(part)
            .on('data', (chunk) => hash.update(chunk))
            .on('end', () => done())
            .on('error', fail)
        })
        have = info.size
      } else {
        await rm(part, { force: true })
      }
    } catch {
      have = 0
    }
    let received: number
    try {
      received = await this.fetchInto(file.url, part, have, file.size, hash, progress)
    } catch (error) {
      if (!(error instanceof AiError && error.reason === 'restart')) throw error
      // El servidor no reanudó: se empieza de cero, una sola vez y con la huella nueva.
      hash = createHash('sha256')
      received = await this.fetchInto(file.url, part, 0, file.size, hash, progress)
    }
    if (received !== file.size || hash.digest('hex') !== file.sha256) {
      await rm(part, { force: true })
      throw new AiError('integrity')
    }
    await rename(part, target)
  }

  private fetchInto(
    url: string,
    part: string,
    offset: number,
    size: number,
    hash: ReturnType<typeof createHash>,
    progress: (bytes: number) => void
  ): Promise<number> {
    return new Promise((done, fail) => {
      let hops = 0
      let finished = false
      const finish = (error: Error | null, value = 0): void => {
        if (finished) return
        finished = true
        this.abort = null
        if (error) fail(error)
        else done(value)
      }
      if (!isModelHost(url)) return finish(new AiError('network'))
      const request = net.request({ url, session: downloadSession(), redirect: 'manual', useSessionCookies: false, credentials: 'omit' })
      if (offset > 0) request.setHeader('Range', `bytes=${offset}-`)
      this.abort = () => {
        request.abort()
        finish(new AiError('cancelled'))
      }
      request.on('redirect', (_status, _method, redirectUrl) => {
        hops++
        // Cada salto se comprueba: solo HTTPS y solo servidores de Hugging Face.
        if (hops > MAX_REDIRECTS || !isModelHost(redirectUrl)) {
          request.abort()
          finish(new AiError('network'))
          return
        }
        request.followRedirect()
      })
      request.on('response', (response) => {
        const resumed = response.statusCode === 206
        if (response.statusCode !== 200 && !resumed) {
          response.on('data', () => undefined)
          finish(new AiError('network'))
          return
        }
        // Sin reanudación (200 en vez de 206) y con una parte ya bajada, la huella
        // ya no sirve: se corta con 'restart' y quien llama empieza de cero.
        let written = resumed ? offset : 0
        if (!resumed && offset > 0) {
          request.abort()
          finish(new AiError('restart'))
          return
        }
        const out = createWriteStream(part, { flags: resumed ? 'a' : 'w' })
        response.on('data', (chunk: Buffer) => {
          written += chunk.length
          // Nunca más de lo anunciado en el catálogo (no llenar el disco).
          if (written > size) {
            request.abort()
            out.destroy()
            finish(new AiError('integrity'))
            return
          }
          hash.update(chunk)
          out.write(chunk)
          progress(written)
        })
        response.on('end', () => out.end(() => finish(null, written)))
        response.on('error', (error) => {
          out.destroy()
          finish(error)
        })
      })
      request.on('error', (error) => finish(error))
      request.end()
    })
  }
}

export class AiError extends Error {
  constructor(readonly reason: AiDownloadError) {
    super(reason)
  }
}

function tierSize(tier: Exclude<AiTier, 'off'>): number {
  return CATALOG.tiers[tier].reduce((sum, id) => sum + CATALOG.components[id].files.reduce((s, f) => s + f.size, 0), 0)
}

function sha256Of(path: string): Promise<string> {
  return new Promise((done, fail) => {
    const hash = createHash('sha256')
    createReadStream(path)
      .on('data', (chunk) => hash.update(chunk))
      .on('end', () => done(hash.digest('hex')))
      .on('error', fail)
  })
}
