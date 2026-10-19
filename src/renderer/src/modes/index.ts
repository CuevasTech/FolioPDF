/**
 * Punto de registro de los modos de trabajo. Cada módulo añade aquí sus barras
 * de herramientas, capas de página, paneles y diálogos.
 */
import { DocBanner } from '@/components/DocBanner'
import { registerToolbar } from '@/components/Toolbar'
import { registerInspector, registerOverlay, registerStage } from '@/components/Workspace'
import { registerKeyHandler } from '@/state/shortcuts'
import { registerLayers } from '@/viewer/ModeLayers'
import { AiPanel } from './ai'
import './ai/personal'
import { AnnotateLayers, AnnotateOptions, AnnotateTools, SelectionActions } from './annotate'
import { EditLayers, EditOptions, EditTools } from './edit'
import { deleteSelection, nudgeSelection } from './edit/objects'
import { FieldInspector, FormLayers, FormOptions, FormTools, ViewLayers } from './form'
import { PagesStage } from './pages'
import { SignLayers, SignOptions, SignTools } from './sign'
import { RedactOverlay, ToolsLayers, ToolsStage, ToolsToolbar } from './tools'

registerLayers('view', ViewLayers)
registerOverlay(DocBanner)

registerToolbar('sign', SignTools)
registerLayers('sign', SignLayers)
registerOverlay(SignOptions)

registerStage('pages', PagesStage)

// «Más» muestra su portada de herramientas; al censurar o recortar vuelve el visor con su barra.
registerStage('tools', ToolsStage, (doc) => doc.tool === '')
registerToolbar('tools', ToolsToolbar)
registerLayers('tools', ToolsLayers)
registerOverlay(RedactOverlay)

registerToolbar('edit', EditTools)
registerLayers('edit', EditLayers)
registerOverlay(EditOptions)

registerToolbar('annotate', AnnotateTools)
registerLayers('annotate', AnnotateLayers)
registerOverlay(AnnotateOptions)
registerOverlay(SelectionActions)

registerToolbar('form', FormTools)
registerLayers('form', FormLayers)
registerOverlay(FormOptions)
registerInspector(FieldInspector)

// IA: el visor normal con el panel del asistente a la derecha.
registerInspector(AiPanel)

// Teclas comunes a todo lo seleccionable: borrar y mover con las flechas.
const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1]
}

registerKeyHandler((event, doc) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return false
  if (event.key === 'Delete' || event.key === 'Backspace') {
    void deleteSelection(doc)
    return true
  }
  const arrow = ARROWS[event.key]
  if (arrow) {
    const step = event.shiftKey ? 10 : 1
    void nudgeSelection(doc, arrow[0] * step, arrow[1] * step)
    return true
  }
  return false
})
