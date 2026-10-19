import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { safeStorage } from 'electron'
import { z } from 'zod'
import type { SavedSignature } from '@shared/api'
import type { RecentFile, Settings } from '@shared/types'
import { log } from './log'

/**
 * Datos persistentes de la aplicación: ajustes, archivos recientes y la
 * biblioteca de firmas manuscritas.
 *
 * Lo que se lee del disco se trata como dato no fiable (puede estar corrupto o
 * manipulado): se valida con un esquema y, si no encaja, se usan los valores
 * por defecto en lugar de fallar (OWASP A08:2025).
 */

/**
 * Dirección del servidor de sello de tiempo: vacía o una URL http(s) normal,
 * sin usuario ni contraseña incrustados. El motor la vuelve a comprobar y además
 * solo se conecta a direcciones públicas (lista blanca, OWASP A05:2025).
 */
function isTimestampUrl(value: string): boolean {
  if (value === '') return true
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname !== '' && url.username === '' && url.password === ''
  } catch {
    return false
  }
}

const SettingsSchema = z.object({
  theme: z.enum(['system', 'light', 'dark']).default('system'),
  accent: z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .default('#4f5bd5'),
  sidebarOpen: z.boolean().default(true),
  highlightFields: z.boolean().default(true),
  defaultZoom: z.enum(['fit-width', 'fit-page', 'actual']).default('fit-width'),
  author: z.string().max(120).default(''),
  timestampUrl: z.string().max(300).refine(isTimestampUrl).default(''),
  signReason: z.string().max(200).default(''),
  signLocation: z.string().max(200).default(''),
  checkUpdates: z.boolean().default(true),
  dateStyle: z.enum(['short', 'shortTime', 'medium', 'long', 'full', 'iso']).default('short'),
  // Por defecto, un servicio de sello de tiempo conocido: la firma con sello funciona sin configurar nada.
  timestampServer: z.enum(['digicert', 'globalsign', 'sectigo', 'custom']).default('digicert'),
  signLevel: z.enum(['basic', 'timestamp', 'longTerm']).default('timestamp'),
  askDefaultPdf: z.boolean().default(true),
  // Solo el nombre de la impresora, que Windows ya conoce; se comprueba al imprimir.
  printer: z.string().max(260).default(''),
  language: z.enum(['auto', 'es', 'en']).default('auto'),
  // IA local: apagada hasta que el usuario la activa y descarga los modelos.
  aiTier: z.enum(['off', 'basic', 'advanced']).default('off'),
  aiGpu: z.boolean().default(true)
})

const BoundsSchema = z.object({
  x: z.number().int().optional(),
  y: z.number().int().optional(),
  width: z.number().int().min(400).max(20000),
  height: z.number().int().min(300).max(20000),
  maximized: z.boolean().default(false)
})

const RecentSchema = z.object({
  id: z.string().regex(/^[0-9a-f]{16}$/),
  path: z.string().min(3).max(32000),
  openedAt: z.number()
})

const ConfigSchema = z.object({
  settings: SettingsSchema.default(SettingsSchema.parse({})),
  window: BoundsSchema.optional(),
  recent: z.array(RecentSchema).max(200).default([])
})

type Config = z.infer<typeof ConfigSchema>
export type WindowBounds = z.infer<typeof BoundsSchema>

const SignatureSchema = z.object({
  id: z.string().regex(/^[0-9a-f]{16}$/),
  kind: z.enum(['signature', 'initials']),
  png: z.string().max(4_000_000), // base64
  width: z.number().positive().max(10000),
  height: z.number().positive().max(10000),
  createdAt: z.number()
})

// La pantalla inicial tiene buscador: caben muchos sin agobiar.
const MAX_RECENT = 100
const MAX_SIGNATURES = 12
const MAX_SIGNATURE_BYTES = 2_000_000
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** Escritura atómica: un corte de luz a mitad no deja el archivo a medias. */
function writeAtomic(file: string, data: string | Buffer): void {
  mkdirSync(dirname(file), { recursive: true })
  const tmp = `${file}.${randomBytes(4).toString('hex')}.tmp`
  try {
    writeFileSync(tmp, data)
    renameSync(tmp, file)
  } finally {
    if (existsSync(tmp)) {
      try {
        unlinkSync(tmp)
      } catch {
        /* nada que hacer */
      }
    }
  }
}

export class Store {
  private config: Config
  private readonly configFile: string
  private readonly signaturesFile: string
  private signatures: z.infer<typeof SignatureSchema>[] | null = null

  constructor(dir: string) {
    this.configFile = join(dir, 'config.json')
    this.signaturesFile = join(dir, 'signatures.bin')
    this.config = this.load()
  }

  private load(): Config {
    try {
      if (existsSync(this.configFile)) {
        const raw = JSON.parse(readFileSync(this.configFile, 'utf8')) as { settings?: Record<string, unknown> }
        // Quien ya había escrito un servidor de sello a mano (versiones anteriores) lo conserva.
        if (raw?.settings && raw.settings.timestampServer === undefined && typeof raw.settings.timestampUrl === 'string' && raw.settings.timestampUrl) {
          raw.settings.timestampServer = 'custom'
        }
        const parsed = ConfigSchema.safeParse(raw)
        if (parsed.success) return parsed.data
        log.warn('ajustes no válidos; se usan los valores por defecto')
      }
    } catch {
      log.warn('no se pudieron leer los ajustes; se usan los valores por defecto')
    }
    return ConfigSchema.parse({})
  }

  private persist(): void {
    try {
      writeAtomic(this.configFile, JSON.stringify(this.config, null, 2))
    } catch {
      log.warn('no se pudieron guardar los ajustes')
    }
  }

  // ---------------------------------------------------------------- ajustes

  get settings(): Settings {
    return { ...this.config.settings }
  }

  updateSettings(patch: unknown): Settings {
    const partial = SettingsSchema.partial().safeParse(patch)
    if (partial.success && typeof patch === 'object' && patch !== null) {
      // Zod 4 rellena con su valor por defecto los ajustes que NO vienen en el
      // cambio; aplicarlos tal cual devolvería los demás a su valor de fábrica.
      // Solo se aplica lo que se ha pedido cambiar.
      const sent = new Set(Object.keys(patch))
      const changes = Object.fromEntries(Object.entries(partial.data).filter(([key]) => sent.has(key)))
      this.config.settings = SettingsSchema.parse({ ...this.config.settings, ...changes })
      this.persist()
    }
    return this.settings
  }

  get windowBounds(): WindowBounds | undefined {
    return this.config.window
  }

  setWindowBounds(bounds: WindowBounds): void {
    const parsed = BoundsSchema.safeParse(bounds)
    if (parsed.success) {
      this.config.window = parsed.data
      this.persist()
    }
  }

  // ----------------------------------------------------------- recientes

  /** Lo que ve la interfaz: nunca recibe la ruta completa, solo un identificador. */
  recentList(): RecentFile[] {
    return this.config.recent.map((entry) => ({
      id: entry.id,
      name: basename(entry.path),
      folder: dirname(entry.path),
      openedAt: entry.openedAt
    }))
  }

  recentPath(id: string): string | null {
    return this.config.recent.find((entry) => entry.id === id)?.path ?? null
  }

  addRecent(path: string): void {
    const key = path.toLowerCase()
    const rest = this.config.recent.filter((entry) => entry.path.toLowerCase() !== key)
    this.config.recent = [{ id: randomBytes(8).toString('hex'), path, openedAt: Date.now() }, ...rest].slice(0, MAX_RECENT)
    this.persist()
  }

  removeRecent(path: string): void {
    const key = path.toLowerCase()
    this.config.recent = this.config.recent.filter((entry) => entry.path.toLowerCase() !== key)
    this.persist()
  }

  /** Quita uno de la lista por su identificador. */
  forgetRecent(id: string): RecentFile[] {
    this.config.recent = this.config.recent.filter((entry) => entry.id !== id)
    this.persist()
    return this.recentList()
  }

  clearRecent(): RecentFile[] {
    this.config.recent = []
    this.persist()
    return []
  }

  // ------------------------------------------------------------- firmas

  /**
   * Las firmas manuscritas guardadas son un dato especialmente sensible: quien
   * las copie puede estamparlas en otros documentos. Se guardan cifradas con la
   * protección de datos de Windows (DPAPI, a través de safeStorage), ligada a la
   * cuenta del usuario; si el cifrado no está disponible, no se guardan en disco
   * (OWASP A04:2025).
   */
  private loadSignatures(): z.infer<typeof SignatureSchema>[] {
    if (this.signatures) return this.signatures
    this.signatures = []
    try {
      if (existsSync(this.signaturesFile) && safeStorage.isEncryptionAvailable()) {
        const json = safeStorage.decryptString(readFileSync(this.signaturesFile))
        const parsed = z.array(SignatureSchema).max(MAX_SIGNATURES).safeParse(JSON.parse(json))
        if (parsed.success) this.signatures = parsed.data
      }
    } catch {
      log.warn('no se pudo leer la biblioteca de firmas')
    }
    return this.signatures
  }

  private persistSignatures(): void {
    if (!this.signatures || !safeStorage.isEncryptionAvailable()) return
    try {
      writeAtomic(this.signaturesFile, safeStorage.encryptString(JSON.stringify(this.signatures)))
    } catch {
      log.warn('no se pudo guardar la biblioteca de firmas')
    }
  }

  signatureList(): SavedSignature[] {
    return this.loadSignatures().map((s) => ({
      id: s.id,
      kind: s.kind,
      png: new Uint8Array(Buffer.from(s.png, 'base64')),
      width: s.width,
      height: s.height,
      createdAt: s.createdAt
    }))
  }

  addSignature(input: { kind: 'signature' | 'initials'; png: Uint8Array; width: number; height: number }): SavedSignature[] {
    const data = Buffer.from(input.png)
    // Solo PNG reales y de tamaño acotado.
    if (data.length < PNG_MAGIC.length || data.length > MAX_SIGNATURE_BYTES || !data.subarray(0, 8).equals(PNG_MAGIC)) {
      return this.signatureList()
    }
    const list = this.loadSignatures()
    list.unshift({
      id: randomBytes(8).toString('hex'),
      kind: input.kind,
      png: data.toString('base64'),
      width: input.width,
      height: input.height,
      createdAt: Date.now()
    })
    this.signatures = list.slice(0, MAX_SIGNATURES)
    this.persistSignatures()
    return this.signatureList()
  }

  removeSignature(id: string): SavedSignature[] {
    this.signatures = this.loadSignatures().filter((s) => s.id !== id)
    this.persistSignatures()
    return this.signatureList()
  }
}
