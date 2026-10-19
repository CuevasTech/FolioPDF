import { create } from 'zustand'
import type { UpdateStatus } from '@shared/types'

/** Estado de las versiones nuevas de Folio, tal como lo cuenta el proceso principal (updates.ts). */
interface UpdatesState {
  status: UpdateStatus
  /** Aviso que el usuario ha dejado para más tarde en esta sesión: la versión («Ahora no»), 'ready' («Más tarde») o 'error'. */
  postponed: string | null
}

export const useUpdates = create<UpdatesState>(() => ({ status: { state: 'unavailable' }, postponed: null }))

let started = false

/** Empieza a seguir el estado (una sola vez, al montar la aplicación). */
export function followUpdates(): void {
  if (started) return
  started = true
  void window.folio.updates.status().then((status) => useUpdates.setState({ status }))
  window.folio.updates.onStatus((status) => useUpdates.setState({ status }))
}

export function postponeUpdate(): void {
  useUpdates.setState((state) => ({ postponed: state.status.version ?? null }))
}
