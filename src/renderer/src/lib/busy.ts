import type { ProgressInfo } from '@shared/types'
import { toastError } from '@/state/actions'
import { useApp } from '@/state/app'
import { isCancelled } from './api'

export interface BusyContext {
  signal: AbortSignal
  onProgress: (info: ProgressInfo) => void
}

/**
 * Ejecuta una operación larga mostrando una ventana de progreso con opción de
 * cancelar. Devuelve `null` si falla o se cancela (el error ya se ha mostrado).
 */
export async function runBusy<T>(title: string, task: (context: BusyContext) => Promise<T>, message = ''): Promise<T | null> {
  const controller = new AbortController()
  const app = useApp.getState()
  app.setBusy({ title, message, progress: null, cancel: () => controller.abort() })
  try {
    return await task({
      signal: controller.signal,
      onProgress: (info) =>
        useApp.getState().setBusy({
          title,
          message: info.message || message,
          progress: info.total > 0 ? info.done / info.total : null,
          cancel: () => controller.abort()
        })
    })
  } catch (error) {
    if (!isCancelled(error)) toastError(error)
    return null
  } finally {
    useApp.getState().setBusy(null)
  }
}
