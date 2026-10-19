/** Tipos de la IA local de Folio que comparten el proceso principal y la interfaz. */

/** Nivel elegido en Ajustes: sin IA, básica (equipos de 8 GB) o avanzada (16 GB o más). */
export type AiTier = 'off' | 'basic' | 'advanced'

/** Qué hace cada modelo. */
export type AiKind = 'llm' | 'embed' | 'ner' | 'unwarp'

export interface AiComponentStatus {
  id: string
  kind: AiKind
  /** Bytes que ocupa (y que hay que descargar si no está). */
  size: number
  license: string
  licenseUrl: string
  installed: boolean
  /** Hay una versión anterior instalada y el catálogo de esta versión de Folio trae otra mejor. */
  update: boolean
}

export interface AiStatus {
  tier: AiTier
  components: AiComponentStatus[]
  /** Todo lo del nivel elegido está instalado y comprobado. */
  ready: boolean
  downloading: boolean
  /** Memoria del equipo en GB, para recomendar el nivel. */
  ramGb: number
  /** Espacio libre en el disco de los modelos, en bytes. */
  freeDisk: number
  /** Lo que ocupa cada nivel completo (para decidir antes de descargar). */
  tierSizes: { basic: number; advanced: number }
  /** Tarjeta gráfica que usa la IA (null: el procesador). */
  gpu?: string | null
  /** El procesador puede ejecutar los modelos (necesitan AVX2). */
  cpuOk?: boolean
}

export interface AiDownloadProgress {
  done: number
  total: number
  component: string
}

/** Motivo de un fallo al descargar los modelos. */
export type AiDownloadError = 'disk' | 'integrity' | 'network' | 'cancelled' | 'restart'
