import { create } from 'zustand'
import type {
  DocDescriptor,
  DocInfo,
  DocMeta,
  DocState,
  FontFamily,
  MutationResult,
  PageInfo,
  Point,
  RecentFile,
  Settings
} from '@shared/types'

export type Mode = 'view' | 'edit' | 'annotate' | 'form' | 'sign' | 'pages' | 'tools' | 'ai'
export type ZoomKind = 'fit-width' | 'fit-page' | 'custom'
export type SidePanel = 'thumbs' | 'outline' | null

export interface Selection {
  kind: 'object' | 'annot' | 'widget'
  page: string
  ids: string[]
}

export interface SearchHitFlat {
  page: string
  quad: Point[]
}

export interface SearchState {
  open: boolean
  query: string
  matchCase: boolean
  wholeWord: boolean
  hits: SearchHitFlat[]
  /** Índices de `hits` agrupados por página. */
  byPage: Record<string, number[]>
  index: number
  truncated: boolean
  busy: boolean
}

export interface DocView {
  id: string
  pages: PageInfo[]
  state: DocState
  meta: DocMeta
  info: DocInfo
  mode: Mode
  /** Herramienta activa dentro del modo ('' = puntero). */
  tool: string
  zoom: { kind: ZoomKind; scale: number }
  /** Página que ocupa el centro de la vista. */
  current: string
  side: SidePanel
  selection: Selection | null
  search: SearchState
}

export type ToastKind = 'info' | 'success' | 'warning' | 'error'

export interface Toast {
  id: number
  kind: ToastKind
  text: string
  action?: { label: string; run: () => void }
}

export interface ConfirmDialog {
  type: 'confirm'
  title: string
  message: string
  confirmLabel: string
  cancelLabel?: string
  danger?: boolean
  /** Tercera opción (p. ej. «No guardar»); recibe el valor de la casilla, si la hay. */
  alt?: { label: string; run: (option: boolean) => void | Promise<void> }
  /** Casilla opcional; su valor llega a ``onConfirm``. */
  option?: { label: string; hint?: string; value: boolean }
  onConfirm: (option: boolean) => void | Promise<void>
  onCancel?: () => void
}

export type DialogState =
  | ConfirmDialog
  | { type: 'password'; token: string; name: string; wrong: boolean }
  | { type: 'unlock'; doc: string; retry?: () => void }
  | { type: 'settings' }
  | { type: 'properties'; doc: string }
  | { type: 'shortcuts' }
  | { type: 'about' }
  | { type: 'custom'; id: string; doc?: string; data?: Record<string, unknown> }

export interface BusyState {
  title: string
  message: string
  /** 0..1, o null si no se conoce el avance. */
  progress: number | null
  cancel?: () => void
}

const EMPTY_SEARCH: SearchState = {
  open: false,
  query: '',
  matchCase: false,
  wholeWord: false,
  hits: [],
  byPage: {},
  index: -1,
  truncated: false,
  busy: false
}

export const DEFAULT_SETTINGS: Settings = {
  theme: 'system',
  accent: '#4f5bd5',
  sidebarOpen: true,
  highlightFields: true,
  defaultZoom: 'fit-width',
  author: '',
  timestampUrl: '',
  signReason: '',
  signLocation: '',
  checkUpdates: true,
  dateStyle: 'short',
  timestampServer: 'digicert',
  signLevel: 'timestamp',
  askDefaultPdf: true,
  closeMany: 'ask',
  printer: '',
  language: 'auto',
  aiTier: 'off',
  aiGpu: true
}

interface AppState {
  ready: boolean
  docs: Record<string, DocView>
  order: string[]
  active: string | null
  settings: Settings
  dark: boolean
  recent: RecentFile[]
  fonts: FontFamily[]
  toasts: Toast[]
  dialog: DialogState | null
  busy: BusyState | null
  dragging: boolean
  /** El motor que lee los PDF está aislado (null: aún no se sabe). */
  isolated: boolean | null

  setReady(settings: Settings, recent: RecentFile[], fonts: FontFamily[]): void
  setFonts(fonts: FontFamily[]): void
  setIsolated(isolated: boolean | null): void
  setSettings(settings: Settings): void
  setDark(dark: boolean): void
  setRecent(recent: RecentFile[]): void
  setDragging(dragging: boolean): void

  addDoc(descriptor: DocDescriptor): void
  removeDoc(id: string): void
  clearDocs(): void
  setActive(id: string): void
  patchDoc(id: string, patch: Partial<DocView>): void
  patchSearch(id: string, patch: Partial<SearchState>): void
  refreshDoc(descriptor: DocDescriptor): void
  applyResult(id: string, result: Pick<MutationResult, 'state'> & Partial<MutationResult>): void

  toast(kind: ToastKind, text: string, action?: Toast['action']): void
  dismissToast(id: number): void
  openDialog(dialog: DialogState): void
  closeDialog(): void
  setBusy(busy: BusyState | null): void
}

let toastSeq = 1

function zoomFromSettings(settings: Settings): DocView['zoom'] {
  if (settings.defaultZoom === 'actual') return { kind: 'custom', scale: 1 }
  return { kind: settings.defaultZoom, scale: 1 }
}

export const useApp = create<AppState>()((set, get) => ({
  ready: false,
  docs: {},
  order: [],
  active: null,
  settings: DEFAULT_SETTINGS,
  dark: false,
  recent: [],
  fonts: [],
  toasts: [],
  dialog: null,
  busy: null,
  dragging: false,
  isolated: null,

  setReady: (settings, recent, fonts) => set({ ready: true, settings, recent, fonts }),
  setFonts: (fonts) => set({ fonts }),
  setIsolated: (isolated) => set({ isolated }),
  setSettings: (settings) => set({ settings }),
  setDark: (dark) => set({ dark }),
  setRecent: (recent) => set({ recent }),
  setDragging: (dragging) => set({ dragging }),

  addDoc: (d) =>
    set((s) => {
      if (s.docs[d.doc]) return { active: d.doc }
      const view: DocView = {
        id: d.doc,
        pages: d.pages,
        state: d.state,
        meta: d.meta,
        info: d.info,
        mode: 'view',
        tool: '',
        zoom: zoomFromSettings(s.settings),
        current: d.pages[0]?.id ?? '',
        side: s.settings.sidebarOpen ? 'thumbs' : null,
        selection: null,
        search: EMPTY_SEARCH
      }
      return { docs: { ...s.docs, [d.doc]: view }, order: [...s.order, d.doc], active: d.doc }
    }),

  removeDoc: (id) =>
    set((s) => {
      if (!s.docs[id]) return s
      const docs = { ...s.docs }
      delete docs[id]
      const index = s.order.indexOf(id)
      const order = s.order.filter((x) => x !== id)
      const active = s.active === id ? (order[Math.min(index, order.length - 1)] ?? null) : s.active
      return { docs, order, active }
    }),

  clearDocs: () => set({ docs: {}, order: [], active: null }),

  setActive: (id) => {
    if (get().docs[id]) set({ active: id })
  },

  patchDoc: (id, patch) =>
    set((s) => (s.docs[id] ? { docs: { ...s.docs, [id]: { ...s.docs[id], ...patch } } } : s)),

  patchSearch: (id, patch) =>
    set((s) =>
      s.docs[id] ? { docs: { ...s.docs, [id]: { ...s.docs[id], search: { ...s.docs[id].search, ...patch } } } } : s
    ),

  refreshDoc: (d) =>
    set((s) =>
      s.docs[d.doc]
        ? { docs: { ...s.docs, [d.doc]: { ...s.docs[d.doc], pages: d.pages, state: d.state, meta: d.meta, info: d.info } } }
        : s
    ),

  applyResult: (id, result) =>
    set((s) => {
      const doc = s.docs[id]
      if (!doc) return s
      let pages = doc.pages
      let current = doc.current
      let selection = doc.selection
      if (result.pages) {
        pages = result.pages
        if (!pages.some((p) => p.id === current)) {
          // La página actual ya no existe: se va a la que ocupa su posición.
          const index = Math.min(doc.pages.findIndex((p) => p.id === current), pages.length - 1)
          current = pages[Math.max(0, index)]?.id ?? ''
        }
        if (selection && !pages.some((p) => p.id === selection!.page)) selection = null
      } else if (result.changed?.length) {
        const gens = new Map(result.changed.map((c) => [c.id, c.gen]))
        pages = pages.map((p) => (gens.has(p.id) && gens.get(p.id) !== p.gen ? { ...p, gen: gens.get(p.id)! } : p))
      }
      return { docs: { ...s.docs, [id]: { ...doc, state: result.state, pages, current, selection } } }
    }),

  toast: (kind, text, action) => {
    const id = toastSeq++
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text, action }] }))
    // Los avisos con botón o con algo que leer con calma duran más.
    const duration = action ? 12000 : kind === 'error' || kind === 'warning' ? 7000 : 4200
    window.setTimeout(() => get().dismissToast(id), duration)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  openDialog: (dialog) => set({ dialog }),
  closeDialog: () => set({ dialog: null }),
  setBusy: (busy) => set({ busy })
}))

/** Documento activo (o undefined en la pantalla de inicio). */
export function useActiveDoc(): DocView | undefined {
  return useApp((s) => (s.active ? s.docs[s.active] : undefined))
}

export function getDoc(id: string): DocView | undefined {
  return useApp.getState().docs[id]
}

