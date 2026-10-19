import type { CallOptions, EngineReply } from '@shared/api'
import type { EngineErrorInfo, Envelope, ProgressInfo } from '@shared/types'
import { L } from '@/i18n'

/** Error devuelto por el motor o por el proceso principal, con su código estable. */
export class FolioError extends Error {
  readonly code: string
  readonly detail?: Record<string, unknown>

  constructor(info: EngineErrorInfo) {
    super(info.message)
    this.name = 'FolioError'
    this.code = info.code
    this.detail = info.detail
  }
}

export function isCancelled(error: unknown): boolean {
  return error instanceof FolioError && (error.code === 'cancelled' || error.code === 'engine_down')
}

/** Mensaje para el usuario a partir de cualquier error. */
export function errorMessage(error: unknown): string {
  if (error instanceof FolioError) return error.message
  return L.state.errors.unexpected
}

export function unwrap<T>(envelope: Envelope<T>): T {
  if (envelope.ok) return envelope.value
  throw new FolioError(envelope.error)
}

let nextCallId = 1
const progressListeners = new Map<number, (info: ProgressInfo) => void>()
let progressBound = false

function bindProgress(): void {
  if (progressBound) return
  progressBound = true
  window.folio.onProgress((info) => progressListeners.get(info.callId)?.(info))
}

export interface CallExtras {
  binary?: Uint8Array
  priority?: number
  signal?: AbortSignal
  onProgress?: (info: ProgressInfo) => void
}

/** Llama a una operación del motor. Lanza `FolioError` si falla. */
export async function call<T = Record<string, unknown>>(
  method: string,
  params: Record<string, unknown> = {},
  extras: CallExtras = {}
): Promise<EngineReply<T>> {
  const options: CallOptions = { binary: extras.binary, priority: extras.priority }
  let callId: number | undefined
  let onAbort: (() => void) | undefined
  if (extras.signal || extras.onProgress) {
    callId = nextCallId++
    options.callId = callId
    if (extras.onProgress) {
      bindProgress()
      progressListeners.set(callId, extras.onProgress)
    }
    if (extras.signal) {
      if (extras.signal.aborted) throw new FolioError({ code: 'cancelled', message: L.state.errors.cancelled })
      const id = callId
      onAbort = () => window.folio.cancel(id)
      extras.signal.addEventListener('abort', onAbort, { once: true })
    }
  }
  try {
    return unwrap(await window.folio.engine<T>(method, params, options))
  } finally {
    if (callId !== undefined) progressListeners.delete(callId)
    if (onAbort) extras.signal?.removeEventListener('abort', onAbort)
  }
}

/** Atajo para operaciones sin carga binaria de respuesta. */
export async function query<T = Record<string, unknown>>(
  method: string,
  params: Record<string, unknown> = {},
  extras: CallExtras = {}
): Promise<T> {
  return (await call<T>(method, params, extras)).result
}

/** Registra un oyente de progreso para llamadas que pasan por el proceso principal. */
export function trackProgress(onProgress: (info: ProgressInfo) => void): { callId: number; done: () => void } {
  bindProgress()
  const callId = nextCallId++
  progressListeners.set(callId, onProgress)
  return { callId, done: () => progressListeners.delete(callId) }
}
