import { create } from 'zustand'
import type { Color, TextStyle } from '@shared/types'

/** Opciones con las que se crean los elementos nuevos; se recuerdan mientras la app está abierta. */
export interface ToolOptions {
  text: TextStyle
  shape: { stroke: Color | null; fill: Color | null; width: number }
  markup: { highlight: Color; underline: Color; strikeout: Color; squiggly: Color }
  draw: { color: Color; width: number; opacity: number }
  note: { color: Color }
  fill: { size: number; color: Color }
}

interface ToolStore extends ToolOptions {
  set<K extends keyof ToolOptions>(key: K, patch: Partial<ToolOptions[K]>): void
}

export const useTools = create<ToolStore>()((set) => ({
  text: { font: 'Arial', bold: false, italic: false, size: 12, color: [0, 0, 0], align: 'left', lineHeight: 1.2 },
  shape: { stroke: [0.12, 0.14, 0.2], fill: null, width: 1.5 },
  markup: {
    highlight: [1, 0.86, 0.2],
    underline: [0.15, 0.6, 0.3],
    strikeout: [0.86, 0.2, 0.22],
    squiggly: [0.86, 0.2, 0.22]
  },
  draw: { color: [0.86, 0.16, 0.2], width: 2, opacity: 1 },
  note: { color: [1, 0.82, 0.2] },
  fill: { size: 11, color: [0.05, 0.1, 0.45] },
  set: (key, patch) => set((state) => ({ [key]: { ...state[key], ...patch } }) as Partial<ToolStore>)
}))

export const PALETTE: Color[] = [
  [0, 0, 0],
  [0.33, 0.36, 0.42],
  [1, 1, 1],
  [0.86, 0.16, 0.2],
  [0.93, 0.47, 0.1],
  [1, 0.82, 0.2],
  [0.15, 0.6, 0.3],
  [0.06, 0.62, 0.62],
  [0.18, 0.45, 0.9],
  [0.05, 0.1, 0.45],
  [0.5, 0.3, 0.8],
  [0.85, 0.3, 0.6]
]

export const HIGHLIGHTS: Color[] = [
  [1, 0.86, 0.2],
  [0.6, 0.93, 0.45],
  [0.5, 0.85, 1],
  [1, 0.65, 0.8],
  [1, 0.7, 0.35]
]
