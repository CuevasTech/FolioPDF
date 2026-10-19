import { create } from 'zustand'
import type { AiDownloadError, AiDownloadProgress, AiStatus } from '@shared/ai'

/** Estado de la IA local tal como lo cuenta el proceso principal (aimodels.ts e ipc.ts). */
interface AiState {
  status: AiStatus | null
  progress: AiDownloadProgress | null
  error: AiDownloadError | null
}

export const useAi = create<AiState>(() => ({ status: null, progress: null, error: null }))

export async function refreshAi(): Promise<void> {
  const reply = await window.folio.ai.status()
  if (reply.ok) useAi.setState({ status: reply.value })
}

let started = false

/** Empieza a seguir el estado (una sola vez, al montar la aplicación). */
export function followAi(): void {
  if (started) return
  started = true
  void refreshAi()
  window.folio.ai.onProgress((progress) => useAi.setState({ progress, ...(progress ? { error: null } : {}) }))
  window.folio.ai.onChanged(() => void refreshAi())
  window.folio.ai.onError((error) => useAi.setState({ error }))
}

/** ¿Puede usarse ya la IA (modelos descargados del nivel elegido y procesador compatible)? */
export function aiReady(status: AiStatus | null): boolean {
  return Boolean(status && status.tier !== 'off' && status.ready && status.cpuOk !== false)
}

/** ¿Está ese tipo de modelo (llm, embed, ner, unwarp) listo? */
export function aiHas(status: AiStatus | null, kind: string): boolean {
  return Boolean(status && status.cpuOk !== false && status.components.some((c) => c.kind === kind && c.installed))
}

const GB = 1024 ** 3

/** Tamaño legible de una descarga (en GB con un decimal, o en MB si es poco). */
export function sizeText(bytes: number, locale: string): string {
  if (bytes >= GB / 2) return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(bytes / GB)} GB`
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(bytes / 1024 ** 2)} MB`
}
