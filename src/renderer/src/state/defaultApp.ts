import { create } from 'zustand'
import type { DefaultAppStatus } from '@shared/types'
import { L } from '@/i18n'
import { useApp } from './app'

/** ¿Abre Windows los PDF con Folio? (ver src/main/defaultApp.ts). */
export const useDefaultApp = create<{ status: DefaultAppStatus; asked: boolean }>(() => ({ status: 'unknown', asked: false }))

let started = false
/** Mientras el usuario está en Configuración de Windows se mira cada poco (ver chooseFolioAsDefault). */
let watching: ReturnType<typeof setInterval> | null = null
const WATCH_EVERY_MS = 2000
const WATCH_FOR_MS = 3 * 60_000

async function refresh(): Promise<void> {
  const status = await window.folio.defaultApp.status()
  // El estado anterior se lee después de esperar: si dos consultas se cruzan,
  // solo la primera que ve el cambio avisa.
  const { status: before, asked } = useDefaultApp.getState()
  if (status === before) return
  useDefaultApp.setState({ status })
  if (status === 'default' && watching) {
    clearInterval(watching)
    watching = null
  }
  // Volviendo de Configuración de Windows tras elegir Folio: se confirma.
  if (asked && before === 'other' && status === 'default') {
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

/**
 * Abre la página de Folio en «Aplicaciones predeterminadas» de Windows. Hasta
 * que el usuario elige Folio (o pasan unos minutos) se vuelve a mirar cada
 * poco, por si al volver la ventana no recibe el foco.
 */
export async function chooseFolioAsDefault(): Promise<void> {
  useDefaultApp.setState({ asked: true })
  if (!(await window.folio.defaultApp.open())) return
  useApp.getState().toast('info', L.state.defaultApp.hint)
  if (watching) clearInterval(watching)
  const until = Date.now() + WATCH_FOR_MS
  watching = setInterval(() => {
    if (Date.now() > until && watching) {
      clearInterval(watching)
      watching = null
      return
    }
    void refresh()
  }, WATCH_EVERY_MS)
}
