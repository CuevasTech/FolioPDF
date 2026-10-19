/**
 * Cómo se reparten las páginas en las hojas al imprimir, como en Acrobat: una
 * página por hoja (ajustada, a tamaño real, reducida o a un porcentaje), varias
 * por hoja, folleto y póster. La misma cuenta sirve para la vista previa (la
 * interfaz) y para lo que se imprime (el proceso principal): lo que se ve es lo
 * que sale.
 *
 * Todas las medidas van en puntos (1/72 de pulgada).
 */

export type PaperName = 'document' | 'A4' | 'A3' | 'A5' | 'Letter' | 'Legal' | 'Tabloid'
export type PrintMode = 'size' | 'multiple' | 'booklet' | 'poster'
export type ScaleMode = 'fit' | 'actual' | 'shrink' | 'custom'
export type SheetOrientation = 'auto' | 'portrait' | 'landscape'
export type PagesPerSheet = 2 | 4 | 6 | 9 | 16

export interface PrintLayout {
  mode: PrintMode
  /** document: cada hoja del tamaño de su página (solo con una página por hoja). */
  paper: PaperName
  orientation: SheetOrientation
  scale: ScaleMode
  /** Porcentaje con scale = custom. */
  customScale: number
  perSheet: PagesPerSheet
  order: 'horizontal' | 'vertical'
  borders: boolean
  /** Varias por hoja: cada hoja lleva la misma página repetida en todos sus huecos (etiquetas, octavillas…). */
  fill: boolean
  bookletSides: 'both' | 'front' | 'back'
  /** Porcentaje al que se amplía cada página en el póster. */
  posterScale: number
}

export const DEFAULT_LAYOUT: PrintLayout = {
  mode: 'size',
  paper: 'A4',
  orientation: 'auto',
  // Como el «Reducir páginas de gran tamaño» de Acrobat (ver single).
  scale: 'shrink',
  customScale: 100,
  perSheet: 2,
  order: 'horizontal',
  borders: false,
  fill: false,
  bookletSides: 'both',
  posterScale: 200
}

/** Tamaños de papel en vertical. Sus nombres, en cada idioma, están en el diccionario (L.print.papers). */
export const PAPERS: Record<Exclude<PaperName, 'document'>, { w: number; h: number }> = {
  A4: { w: 595.28, h: 841.89 },
  A3: { w: 841.89, h: 1190.55 },
  A5: { w: 419.53, h: 595.28 },
  Letter: { w: 612, h: 792 },
  Legal: { w: 612, h: 1008 },
  Tabloid: { w: 792, h: 1224 }
}

/** Margen al ajustar a la hoja (unos 4 mm): la mayoría de impresoras no llegan al borde. */
export const PRINT_MARGIN = 12
/** Separación entre páginas cuando van varias por hoja. */
const GAP = 8
/** Solape entre las hojas de un póster (5 mm), para poder pegarlas. */
export const POSTER_OVERLAP = 14.17
/** Tope de hojas (caras) de un trabajo: un póster muy ampliado se dispara. */
export const MAX_SHEETS = 5000

/** Rejilla de «varias por hoja» en papel vertical: columnas × filas. En horizontal, al revés. */
const GRIDS: Record<PagesPerSheet, [number, number]> = { 2: [1, 2], 4: [2, 2], 6: [2, 3], 9: [3, 3], 16: [4, 4] }

export interface PageSize {
  w: number
  h: number
}

interface Box {
  x: number
  y: number
  w: number
  h: number
}

export interface SheetItem extends Box {
  /** Posición de la página en la lista de las que se imprimen. */
  page: number
  /** 90: la página va girada un cuarto de vuelta en el sentido de las agujas del reloj; la caja ya es la girada. */
  rotate: 0 | 90
  /** Solo se ve lo que cae dentro (póster). */
  clip?: Box
  border?: boolean
}

export interface Sheet {
  w: number
  h: number
  items: SheetItem[]
  /** Folleto: hoja de papel (desde 1) y cara. */
  side?: { sheet: number; back: boolean }
  /** Póster: fila y columna (desde 1). */
  tile?: { row: number; col: number }
}

const clamp = (value: number, min: number, max: number): number => Math.min(max, Math.max(min, value))

function oriented(paper: { w: number; h: number }, landscape: boolean): { w: number; h: number } {
  const short = Math.min(paper.w, paper.h)
  const long = Math.max(paper.w, paper.h)
  return landscape ? { w: long, h: short } : { w: short, h: long }
}

function paperFor(layout: PrintLayout): { w: number; h: number } {
  return layout.paper === 'document' ? PAPERS.A4 : PAPERS[layout.paper]
}

/** La página, lo más grande posible dentro de la caja y centrada; girada si así cabe mayor. */
function placeIn(box: Box, page: PageSize, index: number, allowRotate: boolean): SheetItem {
  const straight = Math.min(box.w / page.w, box.h / page.h)
  const turned = Math.min(box.w / page.h, box.h / page.w)
  const rotate = allowRotate && turned > straight * 1.02
  const s = rotate ? turned : straight
  const w = (rotate ? page.h : page.w) * s
  const h = (rotate ? page.w : page.h) * s
  return { page: index, x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h, rotate: rotate ? 90 : 0 }
}

function single(pages: PageSize[], layout: PrintLayout): Sheet[] {
  return pages.map((page, index) => {
    if (layout.paper === 'document') {
      return { w: page.w, h: page.h, items: [{ page: index, x: 0, y: 0, w: page.w, h: page.h, rotate: 0 }] }
    }
    const landscape = layout.orientation === 'auto' ? page.w > page.h : layout.orientation === 'landscape'
    const sheet = oriented(PAPERS[layout.paper], landscape)
    const fits = Math.min((sheet.w - 2 * PRINT_MARGIN) / page.w, (sheet.h - 2 * PRINT_MARGIN) / page.h)
    let s: number
    if (layout.scale === 'fit') s = fits
    else if (layout.scale === 'actual') s = 1
    else if (layout.scale === 'custom') s = clamp(layout.customScale, 10, 400) / 100
    // Reducir: lo que cabe en el papel sale a su tamaño; lo que no, se ajusta.
    else s = Math.min(1, sheet.w / page.w, sheet.h / page.h) < 1 ? fits : 1
    const w = page.w * s
    const h = page.h * s
    return { ...sheet, items: [{ page: index, x: (sheet.w - w) / 2, y: (sheet.h - h) / 2, w, h, rotate: 0 }] }
  })
}

function grid(sheet: { w: number; h: number }, cols: number, rows: number, order: PrintLayout['order']): Box[] {
  const w = (sheet.w - 2 * PRINT_MARGIN - (cols - 1) * GAP) / cols
  const h = (sheet.h - 2 * PRINT_MARGIN - (rows - 1) * GAP) / rows
  const boxes: Box[] = []
  for (let i = 0; i < cols * rows; i++) {
    const col = order === 'horizontal' ? i % cols : Math.floor(i / rows)
    const row = order === 'horizontal' ? Math.floor(i / cols) : i % rows
    boxes.push({ x: PRINT_MARGIN + col * (w + GAP), y: PRINT_MARGIN + row * (h + GAP), w, h })
  }
  return boxes
}

function multiple(pages: PageSize[], layout: PrintLayout): Sheet[] {
  const paper = paperFor(layout)
  const [cols, rows] = GRIDS[layout.perSheet]
  const options = [false, true].map((landscape) => {
    const sheet = oriented(paper, landscape)
    const boxes = grid(sheet, landscape ? rows : cols, landscape ? cols : rows, layout.order)
    const probe = placeIn(boxes[0], pages[0], 0, true)
    return { landscape, sheet, boxes, score: probe.w * probe.h }
  })
  // Automática: la orientación en la que las páginas salen más grandes.
  const chosen =
    layout.orientation === 'auto'
      ? options[1].score > options[0].score * 1.01
        ? options[1]
        : options[0]
      : options[layout.orientation === 'landscape' ? 1 : 0]
  // Lo que va en cada hueco, en orden. Llenando la hoja, cada página se repite
  // tantas veces como huecos tiene una hoja: una hoja por página.
  const sequence = layout.fill
    ? pages.flatMap((_, index) => Array.from({ length: layout.perSheet }, () => index))
    : pages.map((_, index) => index)
  const sheets: Sheet[] = []
  for (let start = 0; start < sequence.length; start += layout.perSheet) {
    const items = sequence.slice(start, start + layout.perSheet).map((index, i) => ({
      ...placeIn(chosen.boxes[i], pages[index], index, true),
      border: layout.borders
    }))
    sheets.push({ ...chosen.sheet, items })
  }
  return sheets
}

/**
 * Folleto grapado por el centro: dos páginas por cara en papel horizontal, en
 * el orden en que quedan bien al doblar. Si no son múltiplo de cuatro, las
 * últimas caras van en blanco.
 */
function booklet(pages: PageSize[], layout: PrintLayout): Sheet[] {
  const sheet = oriented(paperFor(layout), true)
  const half = sheet.w / 2
  const boxes: Box[] = [0, 1].map((i) => ({
    x: i * half + PRINT_MARGIN,
    y: PRINT_MARGIN,
    w: half - 2 * PRINT_MARGIN,
    h: sheet.h - 2 * PRINT_MARGIN
  }))
  const total = Math.ceil(pages.length / 4) * 4
  const face = (pair: number[], sheetNumber: number, back: boolean): Sheet => ({
    ...sheet,
    side: { sheet: sheetNumber, back },
    items: pair.flatMap((page, i) => (page < pages.length ? [placeIn(boxes[i], pages[page], page, true)] : []))
  })
  const out: Sheet[] = []
  for (let i = 0; i < total / 4; i++) {
    if (layout.bookletSides !== 'back') out.push(face([total - 1 - 2 * i, 2 * i], i + 1, false))
    if (layout.bookletSides !== 'front') out.push(face([2 * i + 1, total - 2 - 2 * i], i + 1, true))
  }
  return out
}

/** Póster: cada página ampliada y repartida en varias hojas que se solapan un poco. */
function poster(pages: PageSize[], layout: PrintLayout): Sheet[] {
  const paper = paperFor(layout)
  const t = clamp(layout.posterScale, 110, 400) / 100
  const out: Sheet[] = []
  pages.forEach((page, index) => {
    const W = page.w * t
    const H = page.h * t
    const options = [false, true].map((landscape) => {
      const sheet = oriented(paper, landscape)
      const tw = sheet.w - 2 * PRINT_MARGIN
      const th = sheet.h - 2 * PRINT_MARGIN
      const cols = Math.max(1, Math.ceil((W - POSTER_OVERLAP) / (tw - POSTER_OVERLAP)))
      const rows = Math.max(1, Math.ceil((H - POSTER_OVERLAP) / (th - POSTER_OVERLAP)))
      return { landscape, sheet, tw, th, cols, rows }
    })
    // Automática: la que necesita menos hojas; a igualdad, la de la página.
    const [portrait, landscape] = options
    const chosen =
      layout.orientation === 'auto'
        ? landscape.cols * landscape.rows < portrait.cols * portrait.rows ||
          (landscape.cols * landscape.rows === portrait.cols * portrait.rows && page.w > page.h)
          ? landscape
          : portrait
        : options[layout.orientation === 'landscape' ? 1 : 0]
    const { sheet, tw, th, cols, rows } = chosen
    // El póster va centrado en el total de las hojas.
    const offX = (cols * (tw - POSTER_OVERLAP) + POSTER_OVERLAP - W) / 2
    const offY = (rows * (th - POSTER_OVERLAP) + POSTER_OVERLAP - H) / 2
    const clip: Box = { x: PRINT_MARGIN, y: PRINT_MARGIN, w: tw, h: th }
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const x = PRINT_MARGIN + offX - col * (tw - POSTER_OVERLAP)
        const y = PRINT_MARGIN + offY - row * (th - POSTER_OVERLAP)
        out.push({ ...sheet, tile: { row: row + 1, col: col + 1 }, items: [{ page: index, x, y, w: W, h: H, rotate: 0, clip }] })
      }
    }
  })
  return out
}

/** Las hojas (caras de papel) de un trabajo de impresión, en el orden en que salen. */
export function layoutSheets(pages: PageSize[], layout: PrintLayout): Sheet[] {
  if (pages.length === 0) return []
  if (layout.mode === 'multiple') return multiple(pages, layout)
  if (layout.mode === 'booklet') return booklet(pages, layout)
  if (layout.mode === 'poster') return poster(pages, layout)
  return single(pages, layout)
}
