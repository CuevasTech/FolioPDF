import { memo, type ReactNode } from 'react'
import type { PageInfo } from '@shared/types'
import type { Mode } from '@/state/app'

export interface LayerProps {
  doc: string
  page: PageInfo
  pxPerPt: number
  tool: string
}

interface Props extends LayerProps {
  mode: Mode
}

const layers = new Map<Mode, (props: LayerProps) => ReactNode>()

/** Cada modo registra la capa interactiva que pone sobre las páginas. */
export function registerLayers(mode: Mode, component: (props: LayerProps) => ReactNode): void {
  layers.set(mode, component)
}

/**
 * Capas interactivas de la página según el modo activo (elementos añadidos,
 * anotaciones, campos de formulario…). Trabajan en píxeles de pantalla para que
 * los tiradores y bordes no cambien de grosor con el zoom.
 */
export const ModeLayers = memo(function ModeLayers({ mode, ...props }: Props) {
  const Layers = layers.get(mode)
  return Layers ? <Layers {...props} /> : null
})
