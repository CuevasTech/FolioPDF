import { randomBytes } from 'node:crypto'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { readFile, stat } from 'node:fs/promises'
import { basename, dirname, extname, join } from 'node:path'
import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent } from 'electron'
import { z } from 'zod'
import type { EngineStatus, ExportOutcome, OpenOutcome, PfxHandle, PickedFile, SaveOutcome } from '@shared/api'
import type { DocDescriptor, EngineErrorInfo, Envelope, MutationResult, UpdateStatus } from '@shared/types'
import type { AiDownloadProgress, AiKind, AiStatus } from '@shared/ai'
import { AiError, type AiModels } from './aimodels'
import { EngineClient, EngineError } from './engine'
import { fmt, L } from './i18n'
import { log } from './log'
import type { PrintService } from './print'
import { isTrustedSender } from './security'
import type { Store } from './store'
import type { Updates } from './updates'
import { openDefaultAppSettings, pdfDefaultStatus } from './defaultApp'
import { feedbackAvailable, sendFeedback } from './feedback'
import { listScanners, scanPages } from './scanner'

/** Código fuente de Folio: con la licencia AGPL, quien recibe la aplicación tiene derecho a él. */
export const SOURCE_URL = 'https://github.com/CuevasTech/folio'

/** Donativos voluntarios: dirección fija aquí, nunca recibida de la interfaz. */
const SPONSORS_URL = 'https://github.com/sponsors/CuevasTech'

/** Licencia y avisos de terceros: junto a Folio.exe una vez instalado; en desarrollo, en el proyecto. */
function legalFile(what: 'license' | 'notices'): string | null {
  const base = app.isPackaged ? dirname(process.execPath) : app.getAppPath()
  const name =
    what === 'license'
      ? app.isPackaged
        ? 'LICENSE.txt'
        : 'LICENSE'
      : app.isPackaged
        ? 'THIRD-PARTY-NOTICES.txt'
        : join('build', 'THIRD-PARTY-NOTICES.txt')
  const file = join(base, name)
  return existsSync(file) ? file : null
}

/**
 * Manejadores de los mensajes que envía la interfaz.
 *
 * Reglas (OWASP A01:2025 / A05:2025):
 * 1. Todo mensaje se valida con un esquema antes de usarse.
 * 2. La interfaz nunca aporta rutas de archivo. Las rutas salen de diálogos
 *    nativos, de archivos soltados en la ventana o de la lista de recientes, y
 *    se quedan en este proceso; la interfaz solo maneja identificadores.
 * 3. Solo puede invocar directamente las operaciones del motor de la lista
 *    PASSTHROUGH, que son las que no reciben rutas.
 */

const PASSTHROUGH = new Set<string>([
  'doc.describe', 'doc.unlock', 'doc.undo', 'doc.redo', 'doc.setMetadata', 'doc.outline', 'doc.search',
  'doc.plainText',
  'page.render', 'page.text', 'page.links', 'page.objects', 'page.annots', 'page.widgets', 'page.editables',
  'fonts.list',
  'obj.addText', 'obj.setText', 'obj.setShape', 'obj.addImage', 'obj.addShape', 'obj.transform', 'obj.delete',
  'obj.order',
  'annot.add', 'annot.addMarkup', 'annot.update', 'annot.delete', 'annot.flatten',
  'form.fields', 'form.setValue', 'form.reset', 'form.flatten', 'form.addFields', 'form.updateField',
  'form.deleteFields', 'form.duplicate', 'form.autoDetect', 'form.exportData',
  'edit.replaceText', 'edit.replaceParagraph', 'edit.deleteText', 'edit.deleteImage', 'edit.replaceImage', 'edit.detachImage',
  'pages.rotate', 'pages.delete', 'pages.move', 'pages.duplicate', 'pages.insertBlank', 'pages.crop',
  'tools.protect', 'tools.unprotect', 'tools.sanitize', 'tools.watermark', 'tools.headerFooter',
  'tools.pageNumbers', 'tools.listAdded', 'tools.removeAdded', 'tools.ocr', 'tools.ocrStatus',
  'redact.mark', 'redact.list', 'redact.unmark', 'redact.find', 'redact.apply',
  'sign.certificates', 'sign.fields', 'sign.addField', 'sign.validate',
  'recovery.list', 'recovery.discard',
  'scan.preview', 'scan.update', 'scan.discard',
  // Detección de datos personales (reglas y, con la IA activada, el modelo de entidades)
  'redact.candidates',
  // IA local. «ai.configure» NO está aquí: solo lo envía el proceso principal.
  'ai.search', 'ai.summarize', 'ai.ask', 'ai.transform', 'ai.addNote'
])

const DocId = z.string().regex(/^d[0-9a-f]{16}$/)
const PageId = z.string().regex(/^p[0-9]{1,9}$/)
const Token = z.string().regex(/^[0-9a-f]{32}$/)
const Binary = z.instanceof(Uint8Array)
const CallId = z.number().int().positive().max(2_000_000_000)
/** Una digitalización en curso y sus páginas (engine/folio_engine/ops/scan.py). */
const ScanJob = z.string().regex(/^j[0-9a-f]{16}$/)
const ScanPage = z.string().regex(/^s[0-9]{1,4}$/)
/** Composición de las hojas al imprimir (src/shared/printLayout.ts), con cada valor acotado. */
const PrintLayoutSchema = z.object({
  mode: z.enum(['size', 'multiple', 'booklet', 'poster']),
  paper: z.enum(['document', 'A4', 'A3', 'A5', 'Letter', 'Legal', 'Tabloid']),
  orientation: z.enum(['auto', 'portrait', 'landscape']),
  scale: z.enum(['fit', 'actual', 'shrink', 'custom']),
  customScale: z.number().min(10).max(400),
  perSheet: z.union([z.literal(2), z.literal(4), z.literal(6), z.literal(9), z.literal(16)]),
  order: z.enum(['horizontal', 'vertical']),
  borders: z.boolean(),
  fill: z.boolean(),
  bookletSides: z.enum(['both', 'front', 'back']),
  posterScale: z.number().min(110).max(400)
})

/** Filtros del diálogo de abrir (es una función: el idioma se fija después de cargar este módulo). */
const openFilters = (): Electron.FileFilter[] => [
  { name: L.main.filters.pdfAndImages, extensions: ['pdf', 'png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'gif', 'webp'] },
  { name: 'PDF', extensions: ['pdf'] }
]
const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'gif', 'webp']
const OPENABLE = new Set(['.pdf', ...IMAGE_EXTENSIONS.map((e) => `.${e}`)])
const MAX_PICKED_IMAGE = 60 * 1024 * 1024

function token(): string {
  return randomBytes(16).toString('hex')
}

function toInfo(error: unknown): EngineErrorInfo {
  if (error instanceof EngineError) return error.toInfo()
  // A la interfaz solo le llega un mensaje genérico, sin rutas ni trazas
  // (A10:2025). Al registro van el tipo y el código del error, no el mensaje: el
  // de un fallo de archivo lleva la ruta, con los nombres del usuario y del
  // documento (A09:2025).
  const code = error instanceof Error ? (error as NodeJS.ErrnoException).code : undefined
  log.error(`error interno: ${error instanceof Error ? error.name : 'desconocido'}${typeof code === 'string' ? ` (${code})` : ''}`)
  return { code: 'internal', message: L.main.errors.unexpected }
}

export function isOpenable(path: string): boolean {
  return OPENABLE.has(extname(path).toLowerCase())
}

export class Bridge {
  private window: BrowserWindow | null = null
  /** Ruta en disco de cada documento abierto (null si aún no se ha guardado). */
  private readonly docs = new Map<string, { path: string | null }>()
  /** Archivos que esperan contraseña para abrirse. */
  private readonly pendingOpens = new Map<string, string>()
  /** Archivos .pfx elegidos por el usuario para firmar. */
  private readonly pfxFiles = new Map<string, string>()
  /** Identificador de llamada de la interfaz → identificador interno del motor. */
  private readonly calls = new Map<number, number>()
  /** Impresiones en preparación que la interfaz puede cancelar, por identificador de llamada. */
  private readonly printCancels = new Map<number, () => void>()
  /** Escaneos en curso que la interfaz puede cancelar, igual. */
  private readonly scanCancels = new Map<number, () => void>()
  private allowClose = false
  private rendererReady = false
  private waitingFiles: string[] = []

  constructor(
    private readonly engine: EngineClient,
    private readonly store: Store,
    private readonly printer: PrintService,
    private readonly updates: Updates,
    private readonly ai: AiModels
  ) {
    this.register()
    engine.on('down', () => {
      this.docs.clear()
      this.send('engine:down')
    })
    engine.on('ready', () => this.send('engine:status', this.engineStatus()))
  }

  private engineStatus(): EngineStatus {
    return { ready: this.engine.isReady, isolated: this.engine.isolated }
  }

  attach(window: BrowserWindow): void {
    this.window = window
    this.allowClose = false
    this.rendererReady = false
    window.on('close', (event) => {
      if (this.allowClose) return
      // La interfaz decide si hay cambios sin guardar antes de cerrar.
      event.preventDefault()
      this.send('window:close-request')
    })
    window.on('closed', () => {
      this.window = null
    })
  }

  /** El motor tiene fuentes nuevas (Aptos): la interfaz vuelve a pedir la lista. */
  sendFontsChanged(): void {
    this.send('fonts:changed')
  }

  /** Avance de la descarga de los modelos de IA (lo emite aimodels.ts). */
  sendAiProgress(progress: AiDownloadProgress | null): void {
    this.send('ai:progress', progress)
  }

  /** Lo que el motor dice de la IA al configurarla: gráfica en uso y si el procesador sirve. */
  private aiEngine: { gpu: string | null; cpuOk: boolean } | null = null

  /**
   * Dice al intermediario qué modelos están listos y si puede usar la gráfica.
   * Las rutas van relativas a la carpeta de modelos y el intermediario vuelve
   * a comprobar que estén dentro; el motor aislado nunca elige qué archivo carga.
   */
  async configureAi(): Promise<void> {
    const tier = this.store.settings.aiTier
    const models: Record<string, { files: Record<string, string>; size: number; ctx?: number }> = {}
    for (const kind of ['llm', 'embed', 'ner', 'unwarp'] as AiKind[]) {
      const ready = await this.ai.ready(tier, kind)
      if (!ready) continue
      const c = ready.component
      models[kind] = {
        files: Object.fromEntries(c.files.map((f) => [f.name.replace(/.[a-z]+$/, ''), `${c.id}/${c.version}/${f.name}`])),
        size: c.files.reduce((sum, f) => sum + f.size, 0),
        ...(c.ctx ? { ctx: c.ctx } : {})
      }
    }
    try {
      const { result } = await this.engine.request<{ gpu?: string | null; cpuOk?: boolean }>(
        'ai.configure',
        { gpu: this.store.settings.aiGpu, models },
        { priority: 9 }
      )
      this.aiEngine = { gpu: result.gpu ?? null, cpuOk: result.cpuOk !== false }
    } catch {
      log.warn('ia: el motor no ha aceptado la configuración')
    }
    this.send('ai:changed')
  }

  private async aiStatus(): Promise<AiStatus> {
    return { ...(await this.ai.status(this.store.settings.aiTier)), gpu: this.aiEngine?.gpu ?? null, cpuOk: this.aiEngine?.cpuOk ?? true }
  }

  /** Estado de las versiones nuevas (lo emite updates.ts). */
  sendUpdate(status: UpdateStatus): void {
    this.send('updates:status', status)
  }

  /** Cierra en el motor los documentos que queden: sus copias de trabajo se borran y no quedan como «recuperables». */
  private closeEngineDocs(): Promise<unknown> {
    const docs = [...this.docs.keys()]
    this.docs.clear()
    return Promise.allSettled(docs.map((doc) => this.engine.request('doc.close', { doc }, { priority: 10 })))
  }

  private send(channel: string, payload?: unknown): void {
    const contents = this.window?.webContents
    if (contents && !contents.isDestroyed()) contents.send(channel, payload)
  }

  /** Registra un manejador que valida el origen y los parámetros, y nunca lanza. */
  private handle<S extends z.ZodTypeAny, R>(
    channel: string,
    schema: S,
    fn: (args: z.infer<S>, event: IpcMainInvokeEvent) => Promise<R> | R
  ): void {
    ipcMain.handle(channel, async (event, raw): Promise<Envelope<R>> => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) {
        return { ok: false, error: { code: 'forbidden', message: L.main.errors.forbiddenOrigin } }
      }
      const parsed = schema.safeParse(raw)
      if (!parsed.success) {
        return { ok: false, error: { code: 'invalid_params', message: L.main.errors.invalidParams } }
      }
      try {
        return { ok: true, value: await fn(parsed.data, event) }
      } catch (error) {
        return { ok: false, error: toInfo(error) }
      }
    })
  }

  // ---------------------------------------------------------------- abrir

  /** Abre archivos cuyas rutas ha aportado el usuario (diálogo, arrastre, sistema). */
  async openPaths(paths: string[], password?: string): Promise<OpenOutcome[]> {
    const outcomes: OpenOutcome[] = []
    for (const path of paths.slice(0, 30)) {
      const name = basename(path)
      const already = [...this.docs.entries()].find(([, entry]) => entry.path?.toLowerCase() === path.toLowerCase())
      try {
        if (already) {
          const { result } = await this.engine.request<DocDescriptor>('doc.describe', { doc: already[0] }, { priority: 9 })
          outcomes.push({ ok: true, doc: result })
          continue
        }
        const params: Record<string, unknown> = { path }
        if (password !== undefined) params.password = password
        const { result } = await this.engine.request<DocDescriptor>('doc.open', params, { priority: 9 })
        // La ruta es la que eligió el usuario, no la que diga el motor: el motor
        // lee PDF ajenos y no se le deja decidir dónde se guardará después
        // (A01:2025). Del motor solo se toma si el documento tiene archivo
        // propio (una imagen abierta se convierte en un PDF nuevo, sin archivo).
        const own = result.state.path ? path : null
        this.docs.set(result.doc, { path: own })
        if (own) this.store.addRecent(own)
        // Solo el número de páginas: ni el nombre ni la ruta van al registro.
        log.info(`documento abierto (${result.pages.length} páginas)`)
        outcomes.push({ ok: true, doc: result })
      } catch (error) {
        const info = toInfo(error)
        if (info.code === 'needs_password' || info.code === 'wrong_password') {
          const id = token()
          this.pendingOpens.set(id, path)
          outcomes.push({ ok: false, name, error: info, token: id })
        } else {
          if (info.code === 'io_error') this.store.removeRecent(path)
          outcomes.push({ ok: false, name, error: info })
        }
      }
    }
    return outcomes
  }

  /** Archivos abiertos desde fuera de la interfaz (doble clic, «Abrir con», segunda instancia). */
  async openFromSystem(paths: string[]): Promise<void> {
    const valid = paths.filter(isOpenable)
    if (valid.length === 0) return
    if (!this.rendererReady) {
      // La interfaz aún no escucha: se abrirán en cuanto avise de que está lista.
      this.waitingFiles.push(...valid)
      return
    }
    this.send('files:opened', await this.openPaths(valid))
  }

  private requireWindow(): BrowserWindow {
    if (!this.window) throw new EngineError({ code: 'internal', message: L.main.errors.noWindow })
    return this.window
  }

  private docPath(doc: string): string | null {
    const entry = this.docs.get(doc)
    if (!entry) throw new EngineError({ code: 'not_found', message: L.main.errors.docClosed })
    return entry.path
  }

  private async docName(doc: string): Promise<string> {
    const { result } = await this.engine.request<DocDescriptor>('doc.describe', { doc }, { priority: 9 })
    return result.state.name
  }

  private async saveTo(doc: string, path: string, force: boolean): Promise<SaveOutcome> {
    const { result } = await this.engine.request<{ state: DocDescriptor['state']; signaturesPreserved: boolean; historyKept?: boolean }>(
      'doc.save',
      { doc, path, force },
      { priority: 9 }
    )
    this.docs.set(doc, { path })
    this.store.addRecent(path)
    return { saved: true, state: result.state, signaturesPreserved: result.signaturesPreserved, historyKept: result.historyKept === true }
  }

  private async saveAs(doc: string, suggestedName?: string): Promise<SaveOutcome> {
    const current = this.docPath(doc)
    const fallbackName = suggestedName ?? (current ? basename(current) : await this.docName(doc))
    const folder = current ? dirname(current) : app.getPath('documents')
    const chosen = await dialog.showSaveDialog(this.requireWindow(), {
      title: L.main.dialogs.savePdf,
      defaultPath: join(folder, fallbackName.toLowerCase().endsWith('.pdf') ? fallbackName : `${fallbackName}.pdf`),
      filters: [{ name: 'PDF', extensions: ['pdf'] }]
    })
    if (chosen.canceled || !chosen.filePath) return { saved: false }
    const path = chosen.filePath.toLowerCase().endsWith('.pdf') ? chosen.filePath : `${chosen.filePath}.pdf`
    // El diálogo del sistema ya ha pedido confirmación si el archivo existía.
    return this.saveTo(doc, path, true)
  }

  private async pickFolder(title: string): Promise<string | null> {
    const chosen = await dialog.showOpenDialog(this.requireWindow(), {
      title,
      properties: ['openDirectory', 'createDirectory']
    })
    return chosen.canceled || chosen.filePaths.length === 0 ? null : chosen.filePaths[0]
  }

  private progressFor(callId: number | undefined) {
    if (callId === undefined) return {}
    return {
      onProgress: (data: { done: number; total: number; message: string; text?: string }) =>
        this.send('engine:progress', { callId, ...data }),
      onId: (id: number) => this.calls.set(callId, id)
    }
  }

  // ------------------------------------------------------------- registro

  private register(): void {
    // ---- motor: operaciones sin rutas
    this.handle(
      'engine:call',
      z.object({
        method: z.string().max(60),
        params: z.record(z.string(), z.unknown()),
        binary: Binary.optional(),
        callId: z.number().int().positive().max(2_000_000_000).optional(),
        priority: z.number().min(0).max(10).optional()
      }),
      async ({ method, params, binary, callId, priority }) => {
        if (!PASSTHROUGH.has(method)) {
          throw new EngineError({ code: 'forbidden', message: L.main.errors.forbiddenOperation })
        }
        try {
          const reply = await this.engine.request(method, params, {
            binary,
            priority: priority ?? 5,
            ...this.progressFor(callId)
          })
          return { result: reply.result, binary: reply.binary }
        } finally {
          if (callId !== undefined) this.calls.delete(callId)
        }
      }
    )

    ipcMain.handle('engine:status', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? this.engineStatus() : { ready: false, isolated: null }
    )

    ipcMain.on('engine:cancel', (event, raw) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return
      const callId = z.number().int().positive().safeParse(raw)
      if (!callId.success) return
      const id = this.calls.get(callId.data)
      if (id !== undefined) this.engine.cancel(id)
      this.printCancels.get(callId.data)?.()
      this.scanCancels.get(callId.data)?.()
    })

    // ---- abrir
    this.handle('files:openDialog', z.undefined(), async () => {
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.common.open,
        properties: ['openFile', 'multiSelections'],
        filters: openFilters()
      })
      return chosen.canceled ? [] : this.openPaths(chosen.filePaths)
    })

    // Las rutas de los archivos soltados las resuelve el preload a partir de los
    // objetos File reales del sistema; la interfaz no puede fabricarlas.
    this.handle('files:openDropped', z.array(z.string().max(32000)).max(30), (paths) =>
      this.openPaths(paths.filter(isOpenable))
    )

    this.handle('files:openRecent', z.string().regex(/^[0-9a-f]{16}$/), async (id) => {
      const path = this.store.recentPath(id)
      if (!path) throw new EngineError({ code: 'not_found', message: L.main.errors.recentGone })
      return this.openPaths([path])
    })

    this.handle('files:retryOpen', z.object({ token: Token, password: z.string().max(1024) }), async ({ token: id, password }) => {
      const path = this.pendingOpens.get(id)
      if (!path) throw new EngineError({ code: 'not_found', message: L.main.errors.openExpired })
      const [outcome] = await this.openPaths([path], password)
      this.pendingOpens.delete(id)
      return outcome
    })

    // ---- guardar y cerrar
    this.handle('files:save', z.object({ doc: DocId, force: z.boolean().optional() }), async ({ doc, force }) => {
      const path = this.docPath(doc)
      return path ? this.saveTo(doc, path, force ?? false) : this.saveAs(doc)
    })

    this.handle('files:saveAs', z.object({ doc: DocId, suggestedName: z.string().max(200).optional() }), ({ doc, suggestedName }) =>
      this.saveAs(doc, suggestedName ? basename(suggestedName) : undefined)
    )

    this.handle('files:close', DocId, async (doc) => {
      this.docs.delete(doc)
      await this.engine.request('doc.close', { doc }, { priority: 9 })
      return null
    })

    this.handle('files:showInFolder', DocId, (doc) => {
      const path = this.docPath(doc)
      if (path) shell.showItemInFolder(path)
      return null
    })

    this.handle('files:pickImage', z.undefined(), async (): Promise<PickedFile | null> => {
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.main.dialogs.pickImage,
        properties: ['openFile'],
        filters: [{ name: L.main.filters.images, extensions: IMAGE_EXTENSIONS }]
      })
      if (chosen.canceled || chosen.filePaths.length === 0) return null
      const path = chosen.filePaths[0]
      const info = await stat(path)
      if (!info.isFile() || info.size > MAX_PICKED_IMAGE) {
        throw new EngineError({ code: 'invalid_file', message: L.main.errors.imageTooLarge })
      }
      return { name: basename(path), data: new Uint8Array(await readFile(path)) }
    })

    // ---- documentos nuevos (aún sin archivo: se guardarán con «Guardar como»)
    const adopt = (doc: DocDescriptor): OpenOutcome => {
      this.docs.set(doc.doc, { path: null })
      return { ok: true, doc }
    }

    // Documento recuperado tras un cierre inesperado: no tiene archivo propio
    // (se guarda con «Guardar como»), igual que uno nuevo.
    this.handle('files:recover', z.object({ id: DocId, password: z.string().max(1024).optional() }), async ({ id, password }) => {
      try {
        const params: Record<string, unknown> = { id }
        if (password !== undefined) params.password = password
        const { result } = await this.engine.request<DocDescriptor>('recovery.open', params, { priority: 9 })
        return adopt(result)
      } catch (error) {
        return { ok: false, name: '', error: toInfo(error) } satisfies OpenOutcome
      }
    })

    this.handle('files:newBlank', z.undefined(), async () => {
      const { result } = await this.engine.request<DocDescriptor>('doc.create', {}, { priority: 9 })
      return adopt(result)
    })

    this.handle('files:merge', z.undefined(), async () => {
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.main.dialogs.pickMerge,
        properties: ['openFile', 'multiSelections'],
        filters: openFilters()
      })
      if (chosen.canceled || chosen.filePaths.length === 0) return null
      if (chosen.filePaths.length < 2) {
        throw new EngineError({ code: 'invalid_params', message: L.main.errors.mergeTwo })
      }
      const { result } = await this.engine.request<DocDescriptor>('doc.merge', { paths: chosen.filePaths }, { priority: 9 })
      return adopt(result)
    })

    this.handle('files:fromImages', z.undefined(), async () => {
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.main.dialogs.pickImages,
        properties: ['openFile', 'multiSelections'],
        filters: [{ name: L.main.filters.images, extensions: IMAGE_EXTENSIONS }]
      })
      if (chosen.canceled || chosen.filePaths.length === 0) return null
      const { result } = await this.engine.request<DocDescriptor>('doc.fromImages', { paths: chosen.filePaths }, { priority: 9 })
      return adopt(result)
    })

    // ---- digitalizar: fotos o escáner → PDF (engine/folio_engine/ops/scan.py)
    this.handle('scan:devices', z.undefined(), () => listScanners())

    this.handle('scan:fromImages', z.object({ job: ScanJob.nullable(), callId: CallId.optional() }), async ({ job, callId }) => {
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.main.dialogs.pickPhotos,
        properties: ['openFile', 'multiSelections'],
        filters: [{ name: L.main.filters.photos, extensions: IMAGE_EXTENSIONS }]
      })
      if (chosen.canceled || chosen.filePaths.length === 0) return null
      // El motor admite 100 por tanda (A06:2025): mejor decirlo que dejar fuera fotos sin avisar.
      if (chosen.filePaths.length > 100) {
        throw new EngineError({ code: 'invalid_params', message: L.main.errors.tooManyPhotos })
      }
      try {
        const params = { ...(job ? { job } : {}), paths: chosen.filePaths, detect: true }
        return (await this.engine.request('scan.add', params, { priority: 9, ...this.progressFor(callId) })).result
      } finally {
        if (callId !== undefined) this.calls.delete(callId)
      }
    })

    this.handle(
      'scan:fromScanner',
      z.object({
        job: ScanJob.nullable(),
        device: z.string().min(1).max(300),
        source: z.enum(['flatbed', 'feeder', 'duplex']),
        color: z.enum(['color', 'gray', 'bw']),
        dpi: z.union([z.literal(150), z.literal(200), z.literal(300)]),
        callId: CallId.optional()
      }),
      async ({ job, callId, ...request }) => {
        const controller = new AbortController()
        if (callId !== undefined) this.scanCancels.set(callId, () => controller.abort())
        // Carpeta propia y vacía para lo que escanee: se borra al terminar, pase lo que pase.
        const folder = mkdtempSync(join(app.getPath('temp'), 'folio-escaneo-'))
        try {
          const pages = await scanPages(request, folder, controller.signal)
          const params = { ...(job ? { job } : {}), paths: pages, detect: false, dpi: request.dpi }
          return (await this.engine.request('scan.add', params, { priority: 9, ...this.progressFor(callId) })).result
        } finally {
          if (callId !== undefined) {
            this.scanCancels.delete(callId)
            this.calls.delete(callId)
          }
          rmSync(folder, { recursive: true, force: true })
        }
      }
    )

    this.handle(
      'scan:build',
      z.object({
        job: ScanJob,
        pages: z.array(ScanPage).min(1).max(300),
        style: z.enum(['auto', 'color', 'gray', 'bw', 'original']),
        ocr: z.boolean(),
        unwarp: z.boolean().default(false),
        callId: CallId.optional()
      }),
      async ({ callId, ...params }) => {
        try {
          const { result } = await this.engine.request<DocDescriptor>('scan.build', params, { priority: 9, ...this.progressFor(callId) })
          return adopt(result)
        } finally {
          if (callId !== undefined) this.calls.delete(callId)
        }
      }
    )

    // ---- páginas con archivos
    this.handle('files:insertPages', z.object({ doc: DocId, before: PageId.nullable() }), async ({ doc, before }) => {
      this.docPath(doc)
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.main.dialogs.insertPages,
        properties: ['openFile'],
        filters: openFilters()
      })
      if (chosen.canceled || chosen.filePaths.length === 0) return null
      const { result } = await this.engine.request<MutationResult>(
        'pages.insertFile',
        { doc, path: chosen.filePaths[0], before },
        { priority: 9 }
      )
      return result
    })

    this.handle('files:extractPages', z.object({ doc: DocId, pages: z.array(PageId).min(1).max(100000) }), async ({ doc, pages }) => {
      const current = this.docPath(doc)
      const name = (current ? basename(current) : await this.docName(doc)).replace(/\.pdf$/i, '')
      const chosen = await dialog.showSaveDialog(this.requireWindow(), {
        title: L.main.dialogs.extractPages,
        defaultPath: join(current ? dirname(current) : app.getPath('documents'), fmt(L.main.fileNames.extract, { name })),
        filters: [{ name: 'PDF', extensions: ['pdf'] }]
      })
      if (chosen.canceled || !chosen.filePath) return { done: false } satisfies ExportOutcome
      const path = chosen.filePath.toLowerCase().endsWith('.pdf') ? chosen.filePath : `${chosen.filePath}.pdf`
      const { result } = await this.engine.request<Record<string, unknown>>('pages.extract', { doc, pages, path }, { priority: 9 })
      return { done: true, files: [path], detail: result } satisfies ExportOutcome
    })

    this.handle(
      'files:splitPages',
      z.object({ doc: DocId, options: z.record(z.string(), z.unknown()), callId: z.number().int().positive().optional() }),
      async ({ doc, options, callId }) => {
        this.docPath(doc)
        const dir = await this.pickFolder(L.main.dialogs.filesFolder)
        if (!dir) return { done: false } satisfies ExportOutcome
        const { result } = await this.engine.request<{ files: string[] } & Record<string, unknown>>(
          'pages.split',
          { ...options, doc, dir },
          { priority: 9, ...this.progressFor(callId) }
        )
        return { done: true, files: result.files, detail: result } satisfies ExportOutcome
      }
    )

    // ---- exportar
    this.handle(
      'files:exportAs',
      z.object({
        doc: DocId,
        kind: z.enum(['optimize', 'images', 'text', 'word']),
        options: z.record(z.string(), z.unknown()),
        callId: z.number().int().positive().optional()
      }),
      async ({ doc, kind, options, callId }) => {
        const current = this.docPath(doc)
        const stem = (current ? basename(current) : await this.docName(doc)).replace(/\.pdf$/i, '')
        const folder = current ? dirname(current) : app.getPath('documents')
        const extra = { priority: 9, ...this.progressFor(callId) }
        try {
          if (kind === 'images') {
            const dir = await this.pickFolder(L.main.dialogs.imagesFolder)
            if (!dir) return { done: false } satisfies ExportOutcome
            const { result } = await this.engine.request<{ files: string[] }>(
              'tools.exportImages',
              { ...options, doc, dir, prefix: stem },
              extra
            )
            return { done: true, files: result.files, detail: result } satisfies ExportOutcome
          }
          const spec = {
            optimize: {
              method: 'tools.optimize',
              ext: 'pdf',
              label: 'PDF',
              suffix: L.main.fileNames.compressedSuffix,
              title: L.main.dialogs.saveCompressed
            },
            text: { method: 'tools.exportText', ext: 'txt', label: L.main.filters.text, suffix: '', title: L.main.dialogs.exportText },
            word: { method: 'tools.exportWord', ext: 'docx', label: 'Word', suffix: '', title: L.main.dialogs.exportWord }
          }[kind]
          const chosen = await dialog.showSaveDialog(this.requireWindow(), {
            title: spec.title,
            defaultPath: join(folder, `${stem}${spec.suffix}.${spec.ext}`),
            filters: [{ name: spec.label, extensions: [spec.ext] }]
          })
          if (chosen.canceled || !chosen.filePath) return { done: false } satisfies ExportOutcome
          const path = chosen.filePath.toLowerCase().endsWith(`.${spec.ext}`) ? chosen.filePath : `${chosen.filePath}.${spec.ext}`
          if (kind === 'optimize' && current && path.toLowerCase() === current.toLowerCase()) {
            throw new EngineError({
              code: 'invalid_params',
              message: L.main.errors.compressedSameName
            })
          }
          const { result } = await this.engine.request<Record<string, unknown>>(spec.method, { ...options, doc, path }, extra)
          return { done: true, files: [path], detail: result } satisfies ExportOutcome
        } finally {
          if (callId !== undefined) this.calls.delete(callId)
        }
      }
    )

    // ---- firma con certificado
    this.handle('sign:pickPfx', z.undefined(), async (): Promise<PfxHandle | null> => {
      const chosen = await dialog.showOpenDialog(this.requireWindow(), {
        title: L.main.dialogs.pickPfx,
        properties: ['openFile'],
        filters: [{ name: L.main.filters.certificate, extensions: ['pfx', 'p12'] }]
      })
      if (chosen.canceled || chosen.filePaths.length === 0) return null
      const id = token()
      this.pfxFiles.set(id, chosen.filePaths[0])
      return { token: id, name: basename(chosen.filePaths[0]) }
    })

    this.handle('sign:pfxInfo', z.object({ token: Token, password: z.string().max(1024) }), async ({ token: id, password }) => {
      const path = this.pfxFiles.get(id)
      if (!path) throw new EngineError({ code: 'not_found', message: L.main.errors.pickPfxAgain })
      const { result } = await this.engine.request('sign.pfxInfo', { path, password }, { priority: 9 })
      return result
    })

    this.handle(
      'sign:sign',
      z.object({ doc: DocId, request: z.record(z.string(), z.unknown()), image: Binary.optional() }),
      async ({ doc, request, image }) => {
        this.docPath(doc)
        const source = z
          .discriminatedUnion('type', [
            z.object({ type: z.literal('store'), thumbprint: z.string().regex(/^[0-9A-Fa-f]{40}$/) }),
            z.object({ type: z.literal('pfx'), token: Token, password: z.string().max(1024) })
          ])
          .parse(request.source)
        let engineSource: Record<string, unknown>
        if (source.type === 'store') {
          // Se pasa el identificador de la ventana para que el aviso de Windows
          // sobre el uso de la clave privada aparezca asociado a la aplicación.
          const handle = this.requireWindow().getNativeWindowHandle()
          const hwnd = handle.length >= 8 ? Number(handle.readBigUInt64LE(0)) : handle.readUInt32LE(0)
          engineSource = { type: 'store', thumbprint: source.thumbprint.toUpperCase() }
          // El motor solo admite identificadores de 32 bits (los de Windows lo
          // son). Si alguna vez no lo fuese, se firma igual sin asociar el aviso.
          if (Number.isSafeInteger(hwnd) && hwnd >= 1 && hwnd <= 0xffffffff) engineSource.hwnd = hwnd
        } else {
          const path = this.pfxFiles.get(source.token)
          if (!path) throw new EngineError({ code: 'not_found', message: L.main.errors.pickPfxAgain })
          engineSource = { type: 'pfx', path, password: source.password }
        }
        const { result } = await this.engine.request<MutationResult>(
          'sign.sign',
          { ...request, doc, source: engineSource },
          { priority: 9, binary: image }
        )
        return result
      }
    )

    // ---- imprimir
    this.handle('print:printers', z.undefined(), () => this.printer.printers(this.requireWindow()))

    this.handle(
      'print:start',
      z.object({
        doc: DocId,
        pages: z.array(PageId).min(1).max(20000),
        // Opciones acotadas: la impresora se comprueba además contra las instaladas (print.ts).
        request: z.discriminatedUnion('mode', [
          z.object({
            mode: z.literal('folio'),
            printer: z.string().min(1).max(260),
            copies: z.number().int().min(1).max(99),
            color: z.boolean(),
            duplex: z.enum(['simplex', 'longEdge', 'shortEdge']),
            layout: PrintLayoutSchema
          }),
          z.object({ mode: z.literal('system'), layout: PrintLayoutSchema })
        ]),
        callId: z.number().int().positive().max(2_000_000_000).optional()
      }),
      async ({ doc, pages, request, callId }) => {
        this.docPath(doc)
        // La cancelación se registra antes de esperar nada: si llega enseguida, no se pierde.
        const controller = new AbortController()
        if (callId !== undefined) this.printCancels.set(callId, () => controller.abort())
        try {
          const { result } = await this.engine.request<DocDescriptor>('doc.describe', { doc }, { priority: 9 })
          // Se respeta la restricción de impresión que haya puesto el autor del PDF.
          if (!result.info.permissions.print) {
            throw new EngineError({ code: 'permission_denied', message: L.main.errors.printNotAllowed })
          }
          await this.printer.print(this.requireWindow(), doc, pages, request, {
            signal: controller.signal,
            onProgress:
              callId === undefined ? undefined : (done, total) => this.send('engine:progress', { callId, done, total, message: '' })
          })
        } finally {
          if (callId !== undefined) this.printCancels.delete(callId)
        }
        return null
      }
    )

    // ---- biblioteca de firmas manuscritas
    ipcMain.handle('signatures:list', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? this.store.signatureList() : []
    )
    ipcMain.handle('signatures:add', (event, raw) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return []
      const parsed = z
        .object({
          kind: z.enum(['signature', 'initials']),
          png: Binary,
          width: z.number().positive().max(10000),
          height: z.number().positive().max(10000)
        })
        .safeParse(raw)
      return parsed.success ? this.store.addSignature(parsed.data) : this.store.signatureList()
    })
    ipcMain.handle('signatures:remove', (event, raw) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return []
      const id = z.string().regex(/^[0-9a-f]{16}$/).safeParse(raw)
      return id.success ? this.store.removeSignature(id.data) : this.store.signatureList()
    })

    // ---- ajustes y recientes
    ipcMain.handle('settings:get', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? this.store.settings : null
    )
    ipcMain.handle('settings:set', (event, raw) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return null
      const before = this.store.settings
      const after = this.store.updateSettings(raw)
      // Cambiar el nivel de IA o la gráfica cambia lo que el motor puede cargar.
      if (after.aiTier !== before.aiTier || after.aiGpu !== before.aiGpu) void this.configureAi()
      return after
    })

    // ---- IA local: modelos (descarga, cancelación, borrado) y estado
    this.handle('ai:status', z.undefined(), () => this.aiStatus())
    this.handle('ai:install', z.undefined(), () => {
      const tier = this.store.settings.aiTier
      if (tier === 'off') return false
      // Sigue en segundo plano; el avance llega por «ai:progress» y el final por «ai:changed» o «ai:error».
      void this.ai
        .install(tier)
        .then(() => this.configureAi())
        .catch((error: unknown) => {
          const reason = error instanceof AiError ? error.reason : 'network'
          if (!(error instanceof AiError)) log.warn(`ia: descarga fallida (${error instanceof Error ? `${error.name}: ${error.message.slice(0, 200)}` : '?'})`)
          this.send('ai:error', reason)
          this.send('ai:changed')
        })
      return true
    })
    this.handle('ai:cancel', z.undefined(), () => {
      this.ai.cancel()
      return true
    })
    this.handle('ai:remove', z.undefined(), async () => {
      // La interfaz pone después el nivel en «Desactivada» (y eso vuelve a configurar el motor).
      await this.ai.removeAll()
      await this.configureAi()
      return true
    })
    ipcMain.handle('recent:list', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? this.store.recentList() : []
    )
    ipcMain.handle('recent:clear', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? this.store.clearRecent() : []
    )
    ipcMain.handle('recent:remove', (event, raw) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return []
      const id = z.string().regex(/^[0-9a-f]{16}$/).safeParse(raw)
      return id.success ? this.store.forgetRecent(id.data) : this.store.recentList()
    })

    // ---- enlaces externos
    ipcMain.handle('shell:openExternal', async (event, raw): Promise<boolean> => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return false
      const parsed = z.string().max(2000).safeParse(raw)
      if (!parsed.success) return false
      let url: URL
      try {
        url = new URL(parsed.data)
      } catch {
        return false
      }
      // Los enlaces vienen del PDF, que no es de fiar: solo se admiten web y
      // correo, y siempre se pregunta mostrando el destino real (A01:2025).
      if (!['http:', 'https:', 'mailto:'].includes(url.protocol)) return false
      const answer = await dialog.showMessageBox(this.requireWindow(), {
        type: 'question',
        buttons: [L.common.open, L.common.cancel],
        defaultId: 1,
        cancelId: 1,
        title: L.main.link.title,
        message: url.protocol === 'mailto:' ? L.main.link.mail : fmt(L.main.link.web, { host: url.host }),
        detail: url.href.slice(0, 400)
      })
      if (answer.response !== 0) return false
      await shell.openExternal(url.href)
      return true
    })

    // ---- ventana
    ipcMain.on('app:ready', (event) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return
      this.rendererReady = true
      log.info('interfaz lista')
      const files = this.waitingFiles.splice(0)
      if (files.length > 0) void this.openFromSystem(files)
    })

    ipcMain.on('window:closeNow', (event) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null)) return
      this.allowClose = true
      // Salida normal: el usuario ya ha decidido qué guardar; lo demás no debe
      // ofrecerse después como «recuperable» (eso es solo para cierres de golpe).
      void this.closeEngineDocs().finally(() => this.window?.close())
    })

    // ---- versiones nuevas: la interfaz solo puede pedir estas cuatro cosas
    ipcMain.handle('updates:status', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? this.updates.current() : { state: 'unavailable' }
    )
    ipcMain.on('updates:check', (event) => {
      if (isTrustedSender(event, this.window?.webContents ?? null)) this.updates.check(true)
    })
    ipcMain.on('updates:download', (event) => {
      if (isTrustedSender(event, this.window?.webContents ?? null)) this.updates.download()
    })
    ipcMain.on('updates:install', (event) => {
      if (!isTrustedSender(event, this.window?.webContents ?? null) || !this.updates.pending) return
      // La interfaz ya ha cerrado los documentos preguntando qué guardar; los
      // que queden en el motor se cierran igual que en una salida normal.
      this.allowClose = true
      void this.closeEngineDocs().finally(() => this.updates.install(true))
    })

    // ---- comentarios: el texto va al servicio de comentarios (feedback.ts), acotado aquí
    ipcMain.handle('feedback:available', (event) => isTrustedSender(event, this.window?.webContents ?? null) && feedbackAvailable())
    this.handle(
      'feedback:send',
      z.object({ kind: z.enum(['Fallo', 'Mejora', 'Otra cosa']), message: z.string().trim().min(3).max(4000) }),
      async ({ kind, message }) => {
        await sendFeedback(kind, message)
        return null
      }
    )

    // ---- programa predeterminado para PDF (solo consultar y abrir Configuración de Windows)
    ipcMain.handle('defaultApp:status', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? pdfDefaultStatus() : 'unknown'
    )
    ipcMain.handle('defaultApp:open', (event) =>
      isTrustedSender(event, this.window?.webContents ?? null) ? openDefaultAppSettings() : false
    )

    // ---- acerca de: licencia, componentes de terceros y código fuente (rutas y
    // dirección fijas, nunca recibidas de la interfaz)
    this.handle('about:open', z.enum(['license', 'notices', 'source', 'issues', 'sponsors']), async (what) => {
      if (what === 'source' || what === 'issues' || what === 'sponsors') {
        await shell.openExternal(what === 'issues' ? `${SOURCE_URL}/issues` : what === 'sponsors' ? SPONSORS_URL : SOURCE_URL)
        return true
      }
      const file = legalFile(what)
      if (!file) return false
      return (await shell.openPath(file)) === ''
    })
  }
}
