import { create } from 'zustand'
import type { DefaultAppStatus } from '@shared/types'
import { L } from '@/i18n'
import { useApp } from './app'

/** ¿Abre Windows los PDF con Folio? (ver src/main/defaultApp.ts). */
export const useDefaultApp = create<{ status: DefaultAppStatus; asked: boolean }>(() => ({ status: 'unknown', asked: false }))

let started = false

async function refresh(): Promise<void> {
  const before = useDefaultApp.getState()
  const status = await window.folio.defaultApp.status()
  useDefaultApp.setState({ status })
  // Volviendo de Configuración de Windows tras elegir Folio: se confirma.
  if (before.asked && before.status === 'other' && status === 'default') {
    useApp.getState().toast('success', L.state.defaultApp.done)
  }
}

/** Mira el estado al arrancar y cada vez que la ventana vuelve a primer plano. */
export function followDefaultApp(): void {
  if (started) return
  started = true
  void refresh()
  window.addEventListener('focus', () => void refresh())
}

/** Abre la página de Folio en «Aplicaciones predeterminadas» de Windows. */
export async function chooseFolioAsDefault(): Promise<void> {
  useDefaultApp.setState({ asked: true })
  if (await window.folio.defaultApp.open()) {
    useApp.getState().toast('info', L.state.defaultApp.hint)
  }
}
