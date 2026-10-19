import type { OpenOutcome } from '@shared/api'
import type { DocDescriptor, FontFamily, MutationResult } from '@shared/types'
import { L, fmt } from '@/i18n'
import { loadExtraFontFaces } from '@/lib/fonts'
import { call, type CallExtras, errorMessage, FolioError, isCancelled, query, unwrap } from '@/lib/api'
import { dropDocCaches } from '@/lib/pageData'
import { getDoc, type Mode, useApp } from './app'
import { refreshSearch } from './search'

const app = () => useApp.getState()

export function toastError(error: unknown): void {
  if (isCancelled(error)) return
  app().toast('error', errorMessage(error))
}

// ------------------------------------------------------------------- arranque

function applyTheme(): void {
  const { settings } = app()
  const dark =
    settings.theme === 'dark' || (settings.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  document.documentElement.style.setProperty('--accent', settings.accent)
  if (app().dark !== dark) app().setDark(dark)
  window.folio.window.setDark(dark)
}

export async function initApp(): Promise<void> {
  // Las escuchas se registran antes de cualquier espera, para no perder los
  // archivos con los que se haya abierto la aplicación.
  window.folio.files.onOpened(handleOutcomes)
  window.folio.window.onCloseRequest(() => void requestQuit())
  window.folio.onEngineDown(() => {
    for (const id of app().order) dropDocCaches(id)
    app().clearDocs()
    app().setBusy(null)
    app().setIsolated(null)
    app().toast('error', L.state.actions.engineRestarted)
  })
  window.folio.onEngineStatus((status) => app().setIsolated(status.isolated))
  void window.folio.engineStatus().then((status) => {
    if (app().isolated === null) app().setIsolated(status.isolated)
  })

  const recent = await window.folio.recent.list()
  let fonts: FontFamily[] = []
  try {
    fonts = (await query<{ fonts: FontFamily[] }>('fonts.list')).fonts
  } catch {
    fonts = [{ family: 'Arial', bold: true, italic: true, boldItalic: true }]
  }
  // Los ajustes se leen al final: mientras arranca el motor (la lista de fuentes
  // puede tardar) el usuario puede haber cambiado alguno, y leerlos antes lo deshacía.
  const settings = await window.folio.settings.get()
  app().setReady(settings ?? app().settings, recent ?? [], fonts)
  void loadExtraFontFaces(fonts)
  // Aptos llega al motor después de arrancar (ver src/main/fonts.ts): entonces se recarga la lista.
  window.folio.onFontsChanged(() => {
    void query<{ fonts: FontFamily[] }>('fonts.list')
      .then(({ fonts: next }) => {
        app().setFonts(next)
        void loadExtraFontFaces(next)
      })
      .catch(() => undefined)
  })
  applyTheme()
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme)
  window.folio.files.ready()
}

export async function updateSettings(patch: Partial<ReturnType<typeof app>['settings']>): Promise<void> {
  const next = await window.folio.settings.set(patch)
  if (next) {
    app().setSettings(next)
    applyTheme()
  }
}

export async function refreshRecent(): Promise<void> {
  app().setRecent((await window.folio.recent.list()) ?? [])
}

// ---------------------------------------------------------------------- abrir

export function handleOutcomes(outcomes: OpenOutcome[]): void {
  let locked: Extract<OpenOutcome, { ok: false }> | null = null
  for (const outcome of outcomes) {
    if (outcome.ok) {
      if (getDoc(outcome.doc.doc)) app().setActive(outcome.doc.doc)
      else app().addDoc(outcome.doc)
    } else if (outcome.token) {
      locked ??= outcome
    } else {
      app().toast('error', fmt(L.state.actions.fileError, { name: outcome.name, message: outcome.error.message }))
    }
  }
  if (locked?.token) {
    app().openDialog({
      type: 'password',
      token: locked.token,
      name: locked.name,
      wrong: locked.error.code === 'wrong_password'
    })
  }
  void refreshRecent()
}

export async function openWithDialog(): Promise<void> {
  try {
    handleOutcomes(unwrap(await window.folio.files.openDialog()))
  } catch (error) {
    toastError(error)
  }
}

export async function openDropped(files: File[]): Promise<void> {
  if (files.length === 0) return
  try {
    handleOutcomes(unwrap(await window.folio.files.openDropped(files)))
  } catch (error) {
    toastError(error)
  }
}

export async function openRecent(id: string): Promise<void> {
  try {
    handleOutcomes(unwrap(await window.folio.files.openRecent(id)))
  } catch (error) {
    toastError(error)
    void refreshRecent()
  }
}

/** Reintenta abrir un PDF protegido. Devuelve `true` si se abrió. */
export async function submitPassword(token: string, password: string): Promise<boolean> {
  try {
    const outcome = unwrap(await window.folio.files.retryOpen(token, password))
    if (outcome.ok) {
      app().closeDialog()
      handleOutcomes([outcome])
      return true
    }
    if (outcome.token) {
      app().openDialog({ type: 'password', token: outcome.token, name: outcome.name, wrong: true })
    } else {
      app().closeDialog()
      app().toast('error', fmt(L.state.actions.fileError, { name: outcome.name, message: outcome.error.message }))
    }
  } catch (error) {
    app().closeDialog()
    toastError(error)
  }
  return false
}

async function openCreated(run: () => Promise<OpenOutcome | null>): Promise<void> {
  try {
    const outcome = await run()
    if (outcome) handleOutcomes([outcome])
  } catch (error) {
    toastError(error)
  }
}

export const newBlank = (): Promise<void> => openCreated(async () => unwrap(await window.folio.files.newBlank()))
export const mergeFiles = (): Promise<void> => openCreated(async () => unwrap(await window.folio.files.merge()))
export const imagesToPdf = (): Promise<void> => openCreated(async () => unwrap(await window.folio.files.fromImages()))

// -------------------------------------------------------------------- guardar

function afterSave(id: string, outcome: { state?: MutationResult['state']; signaturesPreserved?: boolean; historyKept?: boolean }): void {
  if (!outcome.state) return
  const before = getDoc(id)
  app().applyResult(id, { state: outcome.state })
  if (before?.state.signed && !outcome.signaturesPreserved) {
    app().toast('warning', L.state.actions.savedSignaturesBroken)
  } else {
    app().toast('success', L.state.actions.saved)
  }
  // Un documento firmado no se puede reescribir sin romper la firma: se guarda
  // añadiendo los cambios, y lo que se añadió y luego se quitó sigue dentro.
  if (outcome.historyKept) {
    app().toast('info', L.state.actions.historyKept)
  }
  void refreshRecent()
}

export async function saveDoc(id: string, force = false): Promise<boolean> {
  try {
    const outcome = unwrap(await window.folio.files.save(id, force))
    if (!outcome.saved) return false
    afterSave(id, outcome)
    return true
  } catch (error) {
    if (error instanceof FolioError && error.code === 'changed_on_disk') {
      return new Promise<boolean>((resolve) => {
        app().openDialog({
          type: 'confirm',
          title: L.state.actions.changedOnDisk.title,
          message: L.state.actions.changedOnDisk.message,
          confirmLabel: L.state.actions.changedOnDisk.overwrite,
          danger: true,
          alt: { label: L.common.saveAs, run: async () => resolve(await saveDocAs(id)) },
          onConfirm: async () => resolve(await saveDoc(id, true)),
          onCancel: () => resolve(false)
        })
      })
    }
    toastError(error)
    return false
  }
}

export async function saveDocAs(id: string, suggestedName?: string): Promise<boolean> {
  try {
    const outcome = unwrap(await window.folio.files.saveAs(id, suggestedName))
    if (!outcome.saved) return false
    afterSave(id, outcome)
    return true
  } catch (error) {
    toastError(error)
    return false
  }
}

// --------------------------------------------------------------------- cerrar

async function discard(id: string): Promise<void> {
  dropDocCaches(id)
  app().removeDoc(id)
  try {
    unwrap(await window.folio.files.close(id))
  } catch {
    /* si el motor ya no lo tiene abierto, no hay nada que cerrar */
  }
}

/** Cierra un documento preguntando si hay cambios. Devuelve `false` si el usuario cancela. */
export function closeDoc(id: string): Promise<boolean> {
  const doc = getDoc(id)
  if (!doc) return Promise.resolve(true)
  if (!doc.state.dirty) return discard(id).then(() => true)
  app().setActive(id)
  return new Promise<boolean>((resolve) => {
    app().openDialog({
      type: 'confirm',
      title: L.state.actions.unsaved.title,
      message: fmt(L.state.actions.unsaved.message, { name: doc.state.name }),
      confirmLabel: L.common.save,
      alt: {
        label: L.state.actions.unsaved.dontSave,
        run: async () => {
          await discard(id)
          resolve(true)
        }
      },
      onConfirm: async () => {
        if (await saveDoc(id)) {
          await discard(id)
          resolve(true)
        } else {
          resolve(false)
        }
      },
      onCancel: () => resolve(false)
    })
  })
}

let quitting = false

/**
 * Cierra Folio preguntando qué guardar de cada documento; si se cancela en
 * alguno, no se cierra. Con ``install``, en lugar de cerrar sin más se instala
 * la versión nueva ya descargada (y Folio se vuelve a abrir).
 */
export async function requestQuit(options: { install?: boolean } = {}): Promise<void> {
  if (quitting) return
  quitting = true
  try {
    for (const id of [...app().order]) {
      if (!(await closeDoc(id))) return
    }
    if (options.install) window.folio.updates.install()
    else window.folio.window.closeNow()
  } finally {
    quitting = false
  }
}

// ------------------------------------------------------------------- edición

/**
 * Ejecuta una operación que modifica el documento y aplica su resultado.
 * Devuelve `null` si falla (el error ya se ha mostrado al usuario).
 */
export async function mutate<T extends MutationResult = MutationResult>(
  doc: string,
  method: string,
  params: Record<string, unknown> = {},
  extras: CallExtras = {}
): Promise<T | null> {
  try {
    const { result } = await call<T>(method, { doc, ...params }, { priority: 8, ...extras })
    app().applyResult(doc, result)
    if (result.changed?.length || result.pages) refreshSearch(doc)
    return result
  } catch (error) {
    if (error instanceof FolioError && error.code === 'permission_denied') {
      app().openDialog({ type: 'unlock', doc, retry: () => void mutate(doc, method, params, extras) })
    } else {
      toastError(error)
    }
    return null
  }
}

export async function undo(id: string): Promise<void> {
  const doc = getDoc(id)
  if (!doc?.state.canUndo) return
  app().patchDoc(id, { selection: null })
  await mutate(id, 'doc.undo')
}

export async function redo(id: string): Promise<void> {
  const doc = getDoc(id)
  if (!doc?.state.canRedo) return
  app().patchDoc(id, { selection: null })
  await mutate(id, 'doc.redo')
}

export async function unlockDoc(id: string, password: string): Promise<boolean> {
  try {
    const { result } = await call<DocDescriptor>('doc.unlock', { doc: id, password }, { priority: 9 })
    app().refreshDoc(result)
    return true
  } catch (error) {
    if (!(error instanceof FolioError && error.code === 'wrong_password')) toastError(error)
    return false
  }
}

export function setMode(id: string, mode: Mode): void {
  const doc = getDoc(id)
  if (!doc || doc.mode === mode) return
  app().patchDoc(id, { mode, tool: '', selection: null })
}

export function setTool(id: string, tool: string): void {
  app().patchDoc(id, { tool, selection: null })
}
