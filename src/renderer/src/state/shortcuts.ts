import { useEffect } from 'react'
import { viewerOf } from '@/lib/viewerControl'
import { closeDoc, newBlank, openDropped, openWithDialog, redo, saveDoc, saveDocAs, setTool, undo } from './actions'
import { useApp } from './app'
import { printDoc } from './print'
import { closeSearch, openSearch } from './search'

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target.isContentEditable
}

/** Atajos que otros módulos añaden (borrar la selección, moverla con las flechas…). */
type KeyHandler = (event: KeyboardEvent, doc: string) => boolean
const extraHandlers: KeyHandler[] = []

export function registerKeyHandler(handler: KeyHandler): void {
  extraHandlers.push(handler)
}

export function useGlobalShortcuts(): void {
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const state = useApp.getState()
      if (state.busy) return
      const doc = state.active ? state.docs[state.active] : undefined
      const mod = event.ctrlKey || event.metaKey
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key
      const typing = isTyping(event.target)
      let handled = true

      if (mod && key === 'o') void openWithDialog()
      else if (mod && key === 'n') void newBlank()
      else if (mod && key === ',') state.openDialog({ type: 'settings' })
      else if (!doc && !state.dialog && mod && key === 'f') {
        // Sin documentos abiertos, Ctrl+F lleva al buscador de recientes.
        const search = document.querySelector<HTMLInputElement>('.recent-search input')
        if (search) search.focus()
        else handled = false
      } else if (!doc) handled = false
      else if (state.dialog) handled = false
      else if (mod && key === 's') void (event.shiftKey ? saveDocAs(doc.id) : saveDoc(doc.id))
      else if (mod && key === 'w') void closeDoc(doc.id)
      else if (mod && key === 'p') void printDoc(doc.id)
      else if (mod && key === 'f') openSearch(doc.id)
      else if (mod && key === 'Tab') {
        const index = state.order.indexOf(doc.id)
        const next = state.order[(index + (event.shiftKey ? -1 : 1) + state.order.length) % state.order.length]
        if (next) state.setActive(next)
      } else if (mod && (key === '=' || key === '+')) viewerOf(doc.id)?.zoomStep(1)
      else if (mod && key === '-') viewerOf(doc.id)?.zoomStep(-1)
      else if (mod && key === '0') state.patchDoc(doc.id, { zoom: { kind: 'fit-width', scale: doc.zoom.scale } })
      else if (mod && key === '1') state.patchDoc(doc.id, { zoom: { kind: 'custom', scale: 1 } })
      // En un campo de texto, deshacer y rehacer son los del propio campo.
      else if (mod && key === 'z' && !event.shiftKey && !typing) void undo(doc.id)
      else if (mod && (key === 'y' || (key === 'z' && event.shiftKey)) && !typing) void redo(doc.id)
      else if (!typing && extraHandlers.some((handler) => handler(event, doc.id))) handled = true
      else if (key === 'Escape' && !typing) {
        if (doc.selection) state.patchDoc(doc.id, { selection: null })
        else if (doc.tool !== '') setTool(doc.id, '')
        else if (doc.search.open) closeSearch(doc.id)
        else handled = false
      } else handled = false

      if (handled) event.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}

/** Abrir archivos soltándolos sobre la ventana. */
export function useFileDrop(): void {
  useEffect(() => {
    let depth = 0
    const hasFiles = (event: DragEvent): boolean => Array.from(event.dataTransfer?.types ?? []).includes('Files')
    const enter = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      event.preventDefault()
      depth++
      useApp.getState().setDragging(true)
    }
    const over = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const leave = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      depth = Math.max(0, depth - 1)
      if (depth === 0) useApp.getState().setDragging(false)
    }
    const drop = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      event.preventDefault()
      depth = 0
      useApp.getState().setDragging(false)
      void openDropped(Array.from(event.dataTransfer?.files ?? []))
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragover', over)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  }, [])
}
