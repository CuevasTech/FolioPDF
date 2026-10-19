import { randomBytes } from 'node:crypto'
import { BrowserWindow, type WebContentsPrintOptions } from 'electron'
import { layoutSheets, MAX_SHEETS, type PaperName, type Sheet } from '@shared/printLayout'
import type { PrinterEntry, PrintRequest } from '@shared/types'
import type { EngineClient } from './engine'
import { EngineError } from './engine'
import { fmt, L } from './i18n'
import { log } from './log'
import { regQuery } from './registry'
import { APP_SCHEME } from './security'

/**
 * Impresión. El cuadro de imprimir es de Folio (en la interfaz, con vista
 * previa) y se abre al momento: las páginas solo se preparan al pulsar
 * «Imprimir». Las hojas se componen igual que en la vista previa
 * (printLayout.ts): varias páginas por hoja, folleto, póster, papel y escala.
 * Cada página se dibuja a la resolución con la que se imprime, se carga en una
 * ventana oculta cuyo único contenido son esas imágenes y se manda a la
 * impresora elegida sin pasar por el cuadro de Windows (o con él, si el
 * usuario lo pide). La ventana no tiene preload ni acceso a nada.
 */

const DPI = 200
/** Con muchas páginas, algo menos: el trabajo de impresión no crece sin límite. */
const DPI_LARGE = 150
const LARGE_JOB = 100
/** Un póster se dibuja más fino, pero sin pasar de esto: la imagen no crece sin límite. */
const MAX_DPI = 400
/** Píxeles por imagen como mucho (el motor admite 48 millones). */
const MAX_IMAGE_PIXELS = 40_000_000
/** Por debajo del visor (5), para que preparar la impresión no congele lo que se ve. */
const PRIORITY = 4
const TTL_MS = 10 * 60 * 1000

/** Nombres de papel de Electron para cada tamaño de printLayout. */
const PAGE_SIZES: Record<Exclude<PaperName, 'document'>, NonNullable<WebContentsPrintOptions['pageSize']>> = {
  A4: 'A4',
  A3: 'A3',
  A5: 'A5',
  Letter: 'Letter',
  Legal: 'Legal',
  Tabloid: 'Tabloid'
}

interface Job {
  doc: string
  /** Imágenes que hay que dibujar: una por página y escala (una página se repite en las hojas de un póster). */
  images: { page: string; scale: number }[]
  sheets: Sheet[]
  /** Imagen de cada elemento de cada hoja. */
  itemImages: number[][]
  created: number
  served: number
  /** Peticiones de páginas que esperan en el motor: se descartan si se cancela. */
  requests: Set<number>
  onPage?: () => void
}

export interface PrintHooks {
  signal?: AbortSignal
  /** Imágenes preparadas de las que hay que imprimir. */
  onProgress?: (done: number, total: number) => void
}

const cancelled = (): EngineError => new EngineError({ code: 'cancelled', message: L.main.print.cancelled })
const pt = (value: number): string => `${value.toFixed(2)}pt`

/** Impresora predeterminada del usuario según Windows («Nombre,winspool,Ne00:»). */
async function defaultPrinter(): Promise<string | null> {
  const device = await regQuery('HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Windows', 'Device')
  if (!device) return null
  const match = /^(.*),winspool,[^,]*$/i.exec(device)
  return (match ? match[1] : device).trim() || null
}

export class PrintService {
  private readonly jobs = new Map<string, Job>()

  constructor(private readonly engine: EngineClient) {}

  /** Atiende las peticiones a folio://print/<token>/… */
  async respond(url: URL): Promise<Response> {
    const parts = url.pathname.split('/').filter(Boolean)
    const job = this.jobs.get(parts[0] ?? '')
    if (!job) return new Response(null, { status: 404 })
    if (parts.length === 1) return this.html(job)
    const match = /^(\d{1,6})\.jpg$/.exec(parts[1] ?? '')
    const image = match ? job.images[Number(match[1])] : undefined
    if (!image) return new Response(null, { status: 404 })
    let requestId: number | undefined
    try {
      const { binary } = await this.engine.request(
        'page.image',
        { doc: job.doc, page: image.page, scale: image.scale, format: 'jpeg', quality: 92 },
        {
          priority: PRIORITY,
          onId: (id) => {
            requestId = id
            job.requests.add(id)
          }
        }
      )
      if (!binary) return new Response(null, { status: 500 })
      job.served++
      job.onPage?.()
      return new Response(new Uint8Array(binary), { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store' } })
    } catch {
      return new Response(null, { status: 500 })
    } finally {
      if (requestId !== undefined) job.requests.delete(requestId)
    }
  }

  private html(job: Job): Response {
    // Un tamaño de hoja con nombre por cada tamaño distinto: un trabajo puede
    // mezclar hojas verticales y apaisadas.
    const sizes = new Map<string, number>()
    const pages: string[] = []
    const body = job.sheets
      .map((sheet, s) => {
        const key = `${sheet.w.toFixed(1)}x${sheet.h.toFixed(1)}`
        if (!sizes.has(key)) {
          sizes.set(key, sizes.size)
          pages.push(`@page s${sizes.size - 1}{size:${pt(sheet.w)} ${pt(sheet.h)};margin:0}`)
        }
        const items = sheet.items
          .map((item, i) => {
            const src = `${job.itemImages[s][i]}.jpg`
            if (item.clip) {
              // Póster: se ve solo el trozo de página que cae en esta hoja.
              const c = item.clip
              return (
                `<div class="i" style="left:${pt(c.x)};top:${pt(c.y)};width:${pt(c.w)};height:${pt(c.h)}">` +
                `<img src="${src}" alt="" style="left:${pt(item.x - c.x)};top:${pt(item.y - c.y)};width:${pt(item.w)};height:${pt(item.h)}"></div>`
              )
            }
            // Girada: la imagen (sin girar) mide lo contrario que su caja y se gira desde la esquina.
            const image =
              item.rotate === 90
                ? `<img src="${src}" alt="" style="left:${pt(item.w)};top:0;width:${pt(item.h)};height:${pt(item.w)};transform:rotate(90deg);transform-origin:0 0">`
                : `<img src="${src}" alt="" style="left:0;top:0;width:100%;height:100%">`
            return `<div class="i${item.border ? ' b' : ''}" style="left:${pt(item.x)};top:${pt(item.y)};width:${pt(item.w)};height:${pt(item.h)}">${image}</div>`
          })
          .join('')
        return `<div class="s" style="page:s${sizes.get(key)};width:${pt(sheet.w)};height:${pt(sheet.h)}">${items}</div>`
      })
      .join('')
    const html =
      `<!doctype html><html><head><meta charset="utf-8"><title>Folio</title><style>` +
      `${pages.join('')}html,body{margin:0;padding:0}.s{position:relative;overflow:hidden;break-after:page}.s:last-child{break-after:auto}` +
      `.i{position:absolute;overflow:hidden}.i img{position:absolute;display:block}.b{outline:0.6pt solid #555}` +
      `</style></head><body>${body}</body></html>`
    return new Response(html, {
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        // La página de impresión solo puede cargar sus propias imágenes.
        'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
        'X-Content-Type-Options': 'nosniff'
      }
    })
  }

  /** Impresoras instaladas; la predeterminada es la que marca Windows para el usuario. */
  async printers(window: BrowserWindow): Promise<PrinterEntry[]> {
    const [list, preferred] = await Promise.all([window.webContents.getPrintersAsync(), defaultPrinter()])
    return list.map((printer) => ({
      name: printer.name,
      label: printer.displayName || printer.name,
      isDefault: preferred !== null && printer.name.toLowerCase() === preferred.toLowerCase()
    }))
  }

  async print(parent: BrowserWindow, doc: string, wanted: string[], request: PrintRequest, hooks: PrintHooks = {}): Promise<void> {
    const { signal, onProgress } = hooks
    if (request.mode === 'folio') {
      // Solo impresoras que Windows tiene instaladas: el nombre llega de la
      // interfaz y no se manda a ciegas al sistema (A01:2025).
      const installed = await parent.webContents.getPrintersAsync()
      if (!installed.some((printer) => printer.name === request.printer)) {
        throw new EngineError({ code: 'not_found', message: L.main.print.printerGone })
      }
    }
    const { result } = await this.engine.request<{ pages: { id: string; w: number; h: number }[] }>('doc.describe', { doc }, { priority: 9 })
    const byId = new Map(result.pages.map((p) => [p.id, p]))
    const pages = wanted.map((id) => byId.get(id)).filter((p): p is { id: string; w: number; h: number } => Boolean(p))
    if (pages.length === 0) throw new EngineError({ code: 'invalid_params', message: L.main.print.noPages })
    // Las hojas se calculan aquí con los tamaños del motor, no con lo que diga la interfaz.
    const sheets = layoutSheets(pages, request.layout)
    if (sheets.length === 0) throw new EngineError({ code: 'invalid_params', message: L.main.print.noPages })
    if (sheets.length > MAX_SHEETS) {
      throw new EngineError({ code: 'invalid_params', message: fmt(L.main.print.tooManySheets, { max: MAX_SHEETS }) })
    }
    if (signal?.aborted) throw cancelled()

    // Cada página, a la resolución a la que sale en la hoja; las repetidas (póster) se dibujan una vez.
    const dpi = pages.length > LARGE_JOB ? DPI_LARGE : DPI
    const images: Job['images'] = []
    const known = new Map<string, number>()
    const itemImages = sheets.map((sheet) =>
      sheet.items.map((item) => {
        const page = pages[item.page]
        const printed = (item.rotate === 90 ? item.h : item.w) / page.w
        const limit = Math.sqrt(MAX_IMAGE_PIXELS / Math.max(1, page.w * page.h))
        const scale = Math.max(0.05, Math.round(Math.min((dpi / 72) * printed, MAX_DPI / 72, limit) * 100) / 100)
        const key = `${item.page}@${scale}`
        let index = known.get(key)
        if (index === undefined) {
          index = images.push({ page: page.id, scale }) - 1
          known.set(key, index)
        }
        return index
      })
    )

    const now = Date.now()
    for (const [key, job] of this.jobs) if (now - job.created > TTL_MS) this.jobs.delete(key)
    const token = randomBytes(16).toString('hex')
    const job: Job = { doc, images, sheets, itemImages, created: now, served: 0, requests: new Set() }
    job.onPage = () => onProgress?.(Math.min(job.served, images.length), images.length)
    this.jobs.set(token, job)
    onProgress?.(0, images.length)

    const window = new BrowserWindow({
      show: false,
      parent,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, javascript: true, webSecurity: true }
    })
    // Cancelar cierra la ventana oculta y descarta las páginas que esperan en el motor.
    const abort = (): void => {
      this.jobs.delete(token)
      for (const id of job.requests) this.engine.cancel(id)
      if (!window.isDestroyed()) window.destroy()
    }
    signal?.addEventListener('abort', abort, { once: true })
    try {
      await window.loadURL(`${APP_SCHEME}://print/${token}/`)
      // Se espera a que todas las páginas estén descodificadas antes de imprimir.
      await window.webContents.executeJavaScript('Promise.all(Array.from(document.images, (i) => i.decode().catch(() => undefined))).then(() => true)')
      if (signal?.aborted || window.isDestroyed()) throw cancelled()
      const layout = request.layout
      // El papel elegido va también al controlador, y apaisado si lo son todas las hojas.
      const paper: WebContentsPrintOptions =
        layout.paper === 'document' ? {} : { pageSize: PAGE_SIZES[layout.paper], landscape: sheets.every((s) => s.w > s.h) }
      // Un folleto a dos caras solo sale bien girando por el lado corto; por caras sueltas, a una cara.
      const duplex =
        layout.mode === 'booklet' ? (layout.bookletSides === 'both' ? 'shortEdge' : 'simplex') : request.mode === 'folio' ? request.duplex : undefined
      const options: WebContentsPrintOptions =
        request.mode === 'folio'
          ? {
              silent: true,
              deviceName: request.printer,
              copies: request.copies,
              collate: true,
              color: request.color,
              duplexMode: duplex,
              printBackground: true,
              margins: { marginType: 'none' },
              ...paper
            }
          : { silent: false, printBackground: true, margins: { marginType: 'none' }, ...paper, ...(duplex ? { duplexMode: duplex } : {}) }
      const outcome = await new Promise<{ success: boolean; reason: string }>((resolve, reject) => {
        // Si se cancela mientras Windows tiene el trabajo, no se espera más.
        signal?.addEventListener('abort', () => reject(cancelled()), { once: true })
        window.webContents.print(options, (success, reason) => resolve({ success, reason }))
      })
      if (!outcome.success) {
        // Cancelar en el cuadro de Windows no es un error.
        if (outcome.reason === 'Print job canceled') throw cancelled()
        // Solo el motivo genérico de Chromium: nada del documento ni de la impresora (A09:2025).
        log.warn(`impresión no completada: ${outcome.reason}`)
        throw new EngineError({
          code: 'print_failed',
          message: L.main.print.failed
        })
      }
      log.info(`impresión enviada (${pages.length} páginas en ${sheets.length} caras)`)
    } catch (error) {
      if (signal?.aborted) throw cancelled()
      throw error
    } finally {
      signal?.removeEventListener('abort', abort)
      this.jobs.delete(token)
      if (!window.isDestroyed()) window.destroy()
    }
  }
}
