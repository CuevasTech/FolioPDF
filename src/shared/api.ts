/**
 * Contrato del puente entre la interfaz (proceso aislado, sin acceso a Node) y
 * el proceso principal. La interfaz solo puede hacer lo que aparece aquí.
 *
 * Las rutas de archivo nunca viajan desde la interfaz: las elige el usuario en
 * diálogos nativos que abre el proceso principal, que es quien las conserva.
 */

import type { AiDownloadError, AiDownloadProgress, AiStatus } from './ai'
import type { Lang } from './i18n'
import type {
  DocDescriptor,
  DocState,
  EngineErrorInfo,
  Envelope,
  MutationResult,
  PrinterEntry,
  PrintRequest,
  ProgressInfo,
  RecentFile,
  DefaultAppStatus,
  Settings,
  UpdateStatus
} from './types'

export interface EngineReply<T> {
  result: T
  binary?: Uint8Array
}

export interface CallOptions {
  binary?: Uint8Array
  /** Identificador elegido por la interfaz para poder cancelar y recibir el progreso. */
  callId?: number
  /** 0 (puede esperar) … 10 (lo que el usuario acaba de pedir). */
  priority?: number
}

export type OpenOutcome =
  | { ok: true; doc: DocDescriptor }
  | {
      ok: false
      name: string
      error: EngineErrorInfo
      /** Presente cuando se puede reintentar aportando una contraseña. */
      token?: string
    }

export interface SaveOutcome {
  /** Falso si el usuario canceló el diálogo. */
  saved: boolean
  state?: DocState
  signaturesPreserved?: boolean
  /** El archivo firmado conserva dentro los cambios intermedios hechos tras la firma. */
  historyKept?: boolean
}

export interface PickedFile {
  name: string
  data: Uint8Array
}

export interface ExportOutcome {
  /** Falso si el usuario canceló el diálogo. */
  done: boolean
  files?: string[]
  detail?: Record<string, unknown>
}

export interface SavedSignature {
  id: string
  kind: 'signature' | 'initials'
  /** PNG con transparencia. */
  png: Uint8Array
  width: number
  height: number
  createdAt: number
}

export interface PfxHandle {
  token: string
  name: string
}

export interface EngineStatus {
  /** El motor está en marcha y ha saludado. */
  ready: boolean
  /** El proceso que lee los PDF corre aislado (null mientras no se sabe). */
  isolated: boolean | null
}

// ---------------------------------------------------------------- digitalizar

/** Un escáner de Windows (WIA): USB, de red o de una impresora multifunción. */
export interface ScannerInfo {
  id: string
  name: string
}

export interface ScanOptions {
  device: string
  /** Cristal, alimentador, o alimentador a dos caras. */
  source: 'flatbed' | 'feeder' | 'duplex'
  color: 'color' | 'gray' | 'bw'
  dpi: 150 | 200 | 300
}

/** Acabado de las páginas: automático (color solo si lo hay), color, gris, blanco y negro u original. */
export type ScanStyle = 'auto' | 'color' | 'gray' | 'bw' | 'original'

/** Una página de una digitalización en curso. */
export interface ScanPageInfo {
  id: string
  /** Esquinas del papel (relativas a la imagen, de la superior izquierda en el sentido de las agujas del reloj); null: la imagen entera. */
  quad: number[][] | null
  /** Se encontró el papel en la foto. */
  found: boolean
  rotate: 0 | 90 | 180 | 270
  width: number
  height: number
}

export interface ScanAdded {
  job: string
  pages: ScanPageInfo[]
  /** Falso si no se pudo buscar el papel (las fotos quedan enteras). */
  detected: boolean
}

export interface FolioApi {
  /** ``built``: fecha de compilación (AAAA-MM-DD) o vacía si no se conoce. */
  env: { version: string; built: string; dev: boolean; lang: Lang; locale: string }

  /** Llama a una operación del motor que no implica rutas de archivo. */
  engine<T>(method: string, params: Record<string, unknown>, options?: CallOptions): Promise<Envelope<EngineReply<T>>>
  cancel(callId: number): void
  onProgress(listener: (info: ProgressInfo) => void): () => void
  onEngineDown(listener: () => void): () => void
  engineStatus(): Promise<EngineStatus>
  onEngineStatus(listener: (status: EngineStatus) => void): () => void
  /** El motor ha recibido fuentes nuevas (Aptos): hay que volver a pedir la lista. */
  onFontsChanged(listener: () => void): () => void

  files: {
    openDialog(): Promise<Envelope<OpenOutcome[]>>
    openDropped(files: File[]): Promise<Envelope<OpenOutcome[]>>
    openRecent(id: string): Promise<Envelope<OpenOutcome[]>>
    retryOpen(token: string, password: string): Promise<Envelope<OpenOutcome>>
    onOpened(listener: (outcomes: OpenOutcome[]) => void): () => void
    /** Avisa de que la interfaz ya escucha: se entregan los archivos pendientes de abrir. */
    ready(): void
    save(doc: string, force?: boolean): Promise<Envelope<SaveOutcome>>
    saveAs(doc: string, suggestedName?: string): Promise<Envelope<SaveOutcome>>
    close(doc: string): Promise<Envelope<null>>
    showInFolder(doc: string): Promise<Envelope<null>>
    pickImage(): Promise<Envelope<PickedFile | null>>
    /** Abre un documento recuperado tras un cierre inesperado (ver ``recovery.list``). */
    recover(id: string, password?: string): Promise<Envelope<OpenOutcome>>
    newBlank(): Promise<Envelope<OpenOutcome>>
    merge(): Promise<Envelope<OpenOutcome | null>>
    fromImages(): Promise<Envelope<OpenOutcome | null>>
    insertPages(doc: string, before: string | null): Promise<Envelope<MutationResult | null>>
    extractPages(doc: string, pages: string[]): Promise<Envelope<ExportOutcome>>
    splitPages(doc: string, options: Record<string, unknown>, callId?: number): Promise<Envelope<ExportOutcome>>
    exportAs(
      doc: string,
      kind: 'optimize' | 'images' | 'text' | 'word',
      options: Record<string, unknown>,
      callId?: number
    ): Promise<Envelope<ExportOutcome>>
  }

  sign: {
    pickPfx(): Promise<Envelope<PfxHandle | null>>
    pfxInfo(token: string, password: string): Promise<Envelope<unknown>>
    sign(doc: string, request: Record<string, unknown>, image?: Uint8Array): Promise<Envelope<MutationResult>>
  }

  signatures: {
    list(): Promise<SavedSignature[]>
    add(signature: Omit<SavedSignature, 'id' | 'createdAt'>): Promise<SavedSignature[]>
    remove(id: string): Promise<SavedSignature[]>
  }

  print: {
    /** Impresoras instaladas, con la predeterminada de Windows marcada. */
    printers(): Promise<Envelope<PrinterEntry[]>>
    /** Imprime esas páginas. Con callId llega el progreso y se puede cancelar (cancel). */
    start(doc: string, pages: string[], request: PrintRequest, callId?: number): Promise<Envelope<null>>
  }
  /** Digitalizar: fotos o escáner → PDF. Las vistas previas y ajustes van por engine('scan.*'). */
  scan: {
    devices(): Promise<Envelope<ScannerInfo[]>>
    /** Elige fotos y las añade (a una digitalización nueva si job es null); null si se cancela. */
    fromImages(job: string | null, callId?: number): Promise<Envelope<ScanAdded | null>>
    fromScanner(job: string | null, options: ScanOptions, callId?: number): Promise<Envelope<ScanAdded>>
    /** `unwarp`: aplanar las páginas curvadas con la IA (si está activada). */
    build(job: string, pages: string[], style: ScanStyle, ocr: boolean, callId?: number, unwarp?: boolean): Promise<Envelope<OpenOutcome>>
  }

  settings: {
    get(): Promise<Settings>
    set(patch: Partial<Settings>): Promise<Settings>
  }
  recent: {
    list(): Promise<RecentFile[]>
    clear(): Promise<RecentFile[]>
    remove(id: string): Promise<RecentFile[]>
  }
  shell: {
    /** Abre un enlace externo tras pedir confirmación al usuario. */
    openExternal(url: string): Promise<boolean>
  }
  /** Versiones nuevas de Folio: solo se descarga e instala si el usuario lo pide. */
  updates: {
    status(): Promise<UpdateStatus>
    /** Mirar ahora si hay versión nueva (lo pide el usuario). */
    check(): void
    download(): void
    /** Instalar la versión descargada y abrir Folio de nuevo. Antes hay que cerrar los documentos. */
    install(): void
    onStatus(listener: (status: UpdateStatus) => void): () => void
  }
  /** Folio como programa predeterminado para PDF: lo elige siempre el usuario en Configuración de Windows. */
  defaultApp: {
    status(): Promise<DefaultAppStatus>
    /** Abre la página de Folio en «Aplicaciones predeterminadas». */
    open(): Promise<boolean>
  }
  /** Información legal y de la aplicación (licencia, código fuente, componentes de terceros). */
  about: {
    open(what: 'license' | 'notices' | 'source' | 'issues' | 'sponsors'): Promise<boolean>
  }
  /** IA local: modelos (descarga y borrado) y su estado. Lo que hace la IA va por `engine` (métodos ai.*). */
  ai: {
    status(): Promise<Envelope<AiStatus>>
    install(): Promise<Envelope<boolean>>
    cancel(): Promise<Envelope<boolean>>
    remove(): Promise<Envelope<boolean>>
    onProgress(listener: (progress: AiDownloadProgress | null) => void): () => void
    onChanged(listener: () => void): () => void
    onError(listener: (reason: AiDownloadError) => void): () => void
  }
  /** Comentarios al autor (fallos, mejoras…): por correo, a través de su servicio de comentarios. */
  feedback: {
    /** Falso si esta copia no tiene servicio de comentarios: entonces se usa GitHub. */
    available(): Promise<boolean>
    send(kind: 'Fallo' | 'Mejora' | 'Otra cosa', message: string): Promise<Envelope<null>>
  }
  window: {
    setDark(dark: boolean): void
    onCloseRequest(listener: () => void): () => void
    /** Cierra la ventana sin volver a preguntar. */
    closeNow(): void
  }
}
