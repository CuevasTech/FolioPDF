/** Tipos de datos que intercambian el motor, el proceso principal y la interfaz. */

import type { AiTier } from './ai'
import type { LangSetting } from './i18n'
import type { PrintLayout } from './printLayout'

export interface PageInfo {
  id: string
  /** Tamaño visible en puntos (con la rotación ya aplicada). */
  w: number
  h: number
  rot: number
  /** Cambia cada vez que cambia el contenido de la página. */
  gen: number
}

export type ProtectionState = 'none' | 'protected' | 'removing'

export interface DocState {
  token: number
  canUndo: boolean
  canRedo: boolean
  undoLabel: string | null
  redoLabel: string | null
  dirty: boolean
  name: string
  path: string | null
  signed: boolean
  signaturesIntact: boolean
  /** Lo que permiten sus firmas: 1 nada, 2 rellenar y firmar, 3 además comentar; null sin bloqueo. */
  lock: { level: 1 | 2 | 3 | null; fields: boolean }
  protection: ProtectionState
}

export interface DocMeta {
  title: string
  author: string
  subject: string
  keywords: string
  creator: string
  producer: string
  created: string | null
  modified: string | null
}

export type PermissionName =
  | 'print'
  | 'modify'
  | 'copy'
  | 'annotate'
  | 'form'
  | 'accessibility'
  | 'assemble'
  | 'printHq'

export interface DocInfo {
  format: string
  encrypted: boolean
  owner: boolean
  permissions: Record<PermissionName, boolean>
  hasForm: boolean
  hasXfa: boolean
  repaired: boolean
  fileSize: number
}

export interface DocDescriptor {
  doc: string
  pages: PageInfo[]
  state: DocState
  meta: DocMeta
  info: DocInfo
}

/** Respuesta común de toda operación que modifica un documento. */
export interface MutationResult {
  state: DocState
  changed: { id: string; gen: number }[]
  pages?: PageInfo[]
  [extra: string]: unknown
}

export interface EngineErrorInfo {
  code: string
  message: string
  detail?: Record<string, unknown>
}

export type Envelope<T> = { ok: true; value: T } | { ok: false; error: EngineErrorInfo }

export type Rect = [number, number, number, number]
export type Point = [number, number]
export type Color = [number, number, number]

// ---------------------------------------------------------------- texto y enlaces

/** Palabra: caja en coordenadas de visualización y su texto. */
export type Word = [number, number, number, number, string]

export interface TextLine {
  /** Dirección de escritura (1,0 = horizontal). */
  d: [number, number]
  /** Tamaño de fuente. */
  s: number
  w: Word[]
}

export interface PageText {
  lines: TextLine[]
  gen: number
}

export interface PageLink {
  r: Rect
  page?: string
  y?: number
  uri?: string
}

export interface SearchHit {
  page: string
  quads: Point[][]
}

export interface SearchResult {
  hits: SearchHit[]
  total: number
  truncated: boolean
}

export interface OutlineItem {
  level: number
  title: string
  page: string | null
  y: number | null
  bold: boolean
  italic: boolean
}

// ---------------------------------------------------------------- elementos añadidos

export type ObjectKind =
  | 'text'
  | 'image'
  | 'signature'
  | 'shape'
  | 'stamp'
  | 'watermark'
  | 'header'
  | 'footer'
  | 'pagenum'
  | 'ocr'

export interface TextStyle {
  font: string
  bold: boolean
  italic: boolean
  size: number
  color: Color
  /** 'justify' solo tiene efecto con un ancho fijo. */
  align: 'left' | 'center' | 'right' | 'justify'
  lineHeight: number
  /** Ancho de la columna (pt): las líneas se reparten solas en él (un párrafo editado). */
  width?: number
}

export interface TextMetrics {
  padX: number
  padY: number
  lineH: number
  ascent: number
  content: number
}

export interface PlacedObject {
  id: string
  kind: ObjectKind
  cx: number
  cy: number
  w: number
  h: number
  angle: number
  opacity: number
  bbox: Rect
  props: Record<string, unknown>
  metrics?: TextMetrics
}

export interface FontFamily {
  family: string
  bold: boolean
  italic: boolean
  boldItalic: boolean
}

// ---------------------------------------------------------------- ajustes

export type ThemeChoice = 'system' | 'light' | 'dark'

export interface Settings {
  theme: ThemeChoice
  accent: string
  sidebarOpen: boolean
  highlightFields: boolean
  defaultZoom: 'fit-width' | 'fit-page' | 'actual'
  author: string
  timestampUrl: string
  signReason: string
  signLocation: string
  /** Mirar al abrir Folio si hay una versión nueva (solo avisa; descargar e instalar lo decide el usuario). */
  checkUpdates: boolean
  /** Formato de la herramienta «Fecha de hoy». */
  dateStyle: DateStyle
  /** Servicio de sello de tiempo: uno de los conocidos o `custom` (la dirección de `timestampUrl`). */
  timestampServer: TimestampServer
  /** Tipo de firma con certificado que se eligió la última vez. */
  signLevel: SignLevel
  /** Proponer, al abrir Folio, usarlo como programa predeterminado para los PDF. */
  askDefaultPdf: boolean
  /** Impresora usada la última vez ('' = la predeterminada de Windows). */
  printer: string
  /** Idioma de Folio: 'auto' sigue a Windows. Se aplica al volver a abrir Folio. */
  language: LangSetting
  /** IA local: nivel elegido ('off' hasta que el usuario la activa). */
  aiTier: AiTier
  /** Usar una tarjeta gráfica dedicada para la IA (si la hay). */
  aiGpu: boolean
}

/** Una impresora instalada en Windows. */
export interface PrinterEntry {
  /** Nombre del sistema: el que se usa para imprimir. */
  name: string
  /** Nombre para mostrar. */
  label: string
  /** La predeterminada de Windows para este usuario. */
  isDefault: boolean
}

export type DuplexMode = 'simplex' | 'longEdge' | 'shortEdge'

/**
 * Cómo imprimir: con el cuadro de Folio (impresora y opciones elegidas) o con el
 * de Windows. En los dos, las hojas se componen igual (ver printLayout.ts).
 */
export type PrintRequest =
  | { mode: 'folio'; printer: string; copies: number; color: boolean; duplex: DuplexMode; layout: PrintLayout }
  | { mode: 'system'; layout: PrintLayout }

/** ¿Abre Windows los PDF con Folio? unknown: no se puede saber (sin instalar, por ejemplo). */
export type DefaultAppStatus = 'default' | 'other' | 'unknown'

export type TimestampServer = 'digicert' | 'globalsign' | 'sectigo' | 'custom'
/** basic: como AutoFirma o Adobe sin más; timestamp: con sello de tiempo; longTerm: de larga duración (PAdES B-LTA). */
export type SignLevel = 'basic' | 'timestamp' | 'longTerm'

export type DateStyle = 'short' | 'shortTime' | 'medium' | 'long' | 'full' | 'iso'

/** Estado de las versiones nuevas de Folio (ver src/main/updates.ts). */
export interface UpdateStatus {
  /** unavailable: esta copia no se actualiza sola (sin empaquetar); current: ya es la última. */
  state: 'unavailable' | 'idle' | 'checking' | 'available' | 'current' | 'downloading' | 'ready' | 'error'
  version?: string
  percent?: number
  message?: string
}

export interface RecentFile {
  id: string
  name: string
  folder: string
  openedAt: number
}

export interface ProgressInfo {
  callId: number
  done: number
  total: number
  message: string
  /** Texto que la IA lleva generado (se muestra tal cual, como texto plano). */
  text?: string
}
