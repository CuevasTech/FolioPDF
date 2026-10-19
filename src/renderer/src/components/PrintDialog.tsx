import { CircleAlert, Printer } from 'lucide-react'
import { memo, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import {
  DEFAULT_LAYOUT,
  layoutSheets,
  MAX_SHEETS,
  type PagesPerSheet,
  PAPERS,
  type PaperName,
  type PrintLayout,
  type Sheet,
  type SheetItem
} from '@shared/printLayout'
import type { DuplexMode, PageInfo, PrinterEntry, PrintRequest } from '@shared/types'
import { fmt, L, locale, plural } from '@/i18n'
import { errorMessage, isCancelled, trackProgress, unwrap } from '@/lib/api'
import { cn } from '@/lib/util'
import { NumberField } from '@/modes/common/controls'
import { updateSettings } from '@/state/actions'
import { useApp } from '@/state/app'
import { type CustomDialogProps, registerDialog } from './Dialogs'
import { useThumb } from './SidePanel'
import { Checkbox, Dialog, Field, Segmented } from './ui'

/**
 * Cuadro de imprimir de Folio, con vista previa y las opciones de Acrobat:
 * tamaño, varias páginas por hoja, folleto y póster, papel y orientación. Se
 * abre al momento: las páginas se preparan solo al pulsar «Imprimir», en el
 * proceso principal (src/main/print.ts), con la misma composición de hojas que
 * se ve aquí (src/shared/printLayout.ts). El cuadro de Windows queda para los
 * ajustes propios de cada impresora.
 */

type Range = 'all' | 'current' | 'selected' | 'custom'
type Subset = 'all' | 'odd' | 'even'

/** Tamaño máximo de una hoja en la vista previa, en píxeles. */
const PREVIEW_W = 250
const PREVIEW_H = 320
/** Máximo de hojas dibujadas en la vista previa; las demás solo se cuentan. */
const PREVIEW_LIMIT = 200
/** Póster: espacio para la cuadrícula de hojas de cada página y separación entre ellas. */
const POSTER_W = 440
const POSTER_H = 470
const POSTER_GAP = 4

/** Qué hace cada disposición, en una línea. */
const MODE_HINTS: Record<PrintLayout['mode'], string> = L.print.modeHints

/** «1-3, 5, 8-» → posiciones (desde 0), en ese orden y sin repetir; null si no se entiende. */
export function parsePageRange(text: string, count: number): number[] | null {
  const parts = text
    .split(/[,;]/)
    .map((part) => part.trim())
    .filter(Boolean)
  if (parts.length === 0) return null
  const out: number[] = []
  const seen = new Set<number>()
  for (const part of parts) {
    const match = /^(\d{1,6})(\s*-\s*(\d{1,6})?)?$/.exec(part)
    if (!match) return null
    const from = Number(match[1])
    const to = match[2] ? (match[3] ? Number(match[3]) : count) : from
    if (from < 1 || to < from || to > count) return null
    for (let page = from; page <= to; page++) {
      if (seen.has(page)) continue
      seen.add(page)
      out.push(page - 1)
    }
  }
  return out
}

/** Una página colocada en una hoja de la vista previa (girada o recortada como saldrá). */
const PlacedPage = memo(function PlacedPage({ doc, page, item, k }: { doc: string; page: PageInfo; item: SheetItem; k: number }) {
  // La miniatura se dibuja sin girar: su ancho es el de la página en la hoja.
  const { holder, canvas } = useThumb(doc, page, Math.max(8, (item.rotate === 90 ? item.h : item.w) * k))
  if (item.clip) {
    const c = item.clip
    return (
      <div ref={holder} className="print-item" style={{ left: c.x * k, top: c.y * k, width: c.w * k, height: c.h * k }}>
        <canvas ref={canvas} style={{ left: (item.x - c.x) * k, top: (item.y - c.y) * k, width: item.w * k, height: item.h * k }} />
      </div>
    )
  }
  return (
    <div ref={holder} className={cn('print-item', item.border && 'framed')} style={{ left: item.x * k, top: item.y * k, width: item.w * k, height: item.h * k }}>
      <canvas
        ref={canvas}
        style={
          item.rotate === 90
            ? { left: item.w * k, top: 0, width: item.h * k, height: item.w * k, transform: 'rotate(90deg)', transformOrigin: '0 0' }
            : { left: 0, top: 0, width: '100%', height: '100%' }
        }
      />
    </div>
  )
})

const SheetView = memo(function SheetView({
  doc,
  sheet,
  pages,
  label,
  gray,
  k: fixedK,
  badge
}: {
  doc: string
  sheet: Sheet
  pages: PageInfo[]
  label?: string
  gray: boolean
  /** Escala propia (la cuadrícula del póster); si no, la de una hoja suelta. */
  k?: number
  /** Número de la hoja dentro del póster. */
  badge?: string
}) {
  const k = fixedK ?? Math.min(PREVIEW_W / sheet.w, PREVIEW_H / sheet.h)
  return (
    <div className="print-sheet">
      <div className={cn('print-paper', gray && 'gray')} style={{ width: sheet.w * k, height: sheet.h * k }}>
        {sheet.items.map((item, i) => (
          <PlacedPage key={i} doc={doc} page={pages[item.page]} item={item} k={k} />
        ))}
        {badge && <span className="print-badge">{badge}</span>}
      </div>
      {label && <span>{label}</span>}
    </div>
  )
})

/**
 * Póster: las hojas de cada página juntas y en su sitio, como quedarán al
 * pegarlas, numeradas en el orden en que salen de la impresora.
 */
function PosterPreview({
  doc,
  sheets,
  pages,
  numberOf,
  gray
}: {
  doc: string
  sheets: Sheet[]
  pages: PageInfo[]
  numberOf: Map<string, number>
  gray: boolean
}) {
  // Las hojas de una misma página van seguidas, fila a fila.
  const groups: Sheet[][] = []
  for (const sheet of sheets.slice(0, PREVIEW_LIMIT)) {
    const last = groups[groups.length - 1]
    if (last && last[0].items[0].page === sheet.items[0].page) last.push(sheet)
    else groups.push([sheet])
  }
  return (
    <>
      {groups.map((group) => {
        const cols = Math.max(...group.map((sheet) => sheet.tile?.col ?? 1))
        const rows = Math.max(...group.map((sheet) => sheet.tile?.row ?? 1))
        const { w, h } = group[0]
        const k = Math.min((POSTER_W - (cols - 1) * POSTER_GAP) / (cols * w), (POSTER_H - (rows - 1) * POSTER_GAP) / (rows * h), PREVIEW_W / w)
        const page = pages[group[0].items[0].page]
        return (
          <div key={group[0].items[0].page} className="print-poster">
            <div className="print-poster-grid" style={{ gridTemplateColumns: `repeat(${cols}, ${w * k}px)`, gap: POSTER_GAP }}>
              {group.map((sheet, i) => (
                <SheetView key={i} doc={doc} sheet={sheet} pages={pages} gray={gray} k={k} badge={String(i + 1)} />
              ))}
            </div>
            <span>
              {pages.length > 1
                ? plural(L.print.poster.sheetsOfPage, group.length, { page: numberOf.get(page.id) ?? '', cols, rows })
                : plural(L.print.poster.sheets, group.length, { cols, rows })}
            </span>
          </div>
        )
      })}
      {sheets.length > PREVIEW_LIMIT && <span className="print-empty">{plural(L.print.moreSheets, sheets.length - PREVIEW_LIMIT)}</span>}
    </>
  )
}

/** Un grupo de opciones con su título: separa a simple vista lo que va junto. */
function PrintGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="print-group" aria-label={title}>
      <h3>{title}</h3>
      {children}
    </section>
  )
}

const PAPER_OPTIONS = Object.keys(PAPERS) as Exclude<PaperName, 'document'>[]

/** Las opciones de la disposición elegida, juntas en su recuadro bajo una línea que dice qué hace. */
function ModeOptions({ layout, set, busy }: { layout: PrintLayout; set: (patch: Partial<PrintLayout>) => void; busy: boolean }) {
  const T = L.print
  return (
    <div className="print-mode">
      <p className="print-mode-hint">{MODE_HINTS[layout.mode]}</p>
      {layout.mode === 'size' && (
        <div className="print-ranges">
          {(
            [
              ['fit', T.scale.fit],
              ['actual', T.scale.actual],
              ['shrink', T.scale.shrink],
              ['custom', T.scale.custom]
            ] as const
          ).map(([value, label]) => (
            // El porcentaje va fuera de la etiqueta: el estilo de los botones de opción no le afecta.
            <div key={value} className="print-scale">
              <label className="check">
                <input type="radio" name="print-scale" checked={layout.scale === value} disabled={busy || layout.paper === 'document'} onChange={() => set({ scale: value })} />
                <span>{label}</span>
              </label>
              {value === 'custom' && (
                <NumberField value={layout.customScale} onChange={(customScale) => set({ scale: 'custom', customScale: Math.round(customScale) })} min={10} max={400} step={5} width={58} suffix="%" tip={T.scale.tip} />
              )}
            </div>
          ))}
        </div>
      )}
      {layout.mode === 'multiple' && (
        <>
          <div className="print-row">
            <Field label={T.multiple.perSheet}>
              <select className="select" aria-label={T.multiple.perSheetLabel} value={layout.perSheet} disabled={busy} onChange={(event) => set({ perSheet: Number(event.target.value) as PagesPerSheet })}>
                {[2, 4, 6, 9, 16].map((n) => (
                  <option key={n} value={n}>
                    {plural(L.common.pageCount, n)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={T.multiple.order}>
              {/* Si se repite cada página hasta llenar la hoja, el orden no cambia nada. */}
              <select
                className="select"
                aria-label={T.multiple.orderLabel}
                value={layout.order}
                disabled={busy || layout.fill}
                onChange={(event) => set({ order: event.target.value as PrintLayout['order'] })}
              >
                <option value="horizontal">{T.multiple.byRows}</option>
                <option value="vertical">{T.multiple.byColumns}</option>
              </select>
            </Field>
          </div>
          <Checkbox checked={layout.fill} disabled={busy} onChange={(fill) => set({ fill })}>
            {T.multiple.fill}
          </Checkbox>
          <Checkbox checked={layout.borders} disabled={busy} onChange={(borders) => set({ borders })}>
            {T.multiple.borders}
          </Checkbox>
        </>
      )}
      {layout.mode === 'booklet' && (
        <Field label={T.booklet.sides} hint={T.booklet.sidesHint}>
          <select className="select" aria-label={T.booklet.sidesLabel} value={layout.bookletSides} disabled={busy} onChange={(event) => set({ bookletSides: event.target.value as PrintLayout['bookletSides'] })}>
            <option value="both">{T.booklet.both}</option>
            <option value="front">{T.booklet.front}</option>
            <option value="back">{T.booklet.back}</option>
          </select>
        </Field>
      )}
      {layout.mode === 'poster' && (
        <Field label={T.poster.scale} hint={T.poster.scaleHint}>
          <NumberField value={layout.posterScale} onChange={(posterScale) => set({ posterScale: Math.round(posterScale) })} min={110} max={400} step={10} width={58} suffix="%" tip={T.poster.scaleTip} />
        </Field>
      )}
    </div>
  )
}

function PrintDialog({ doc: docId, data }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const toast = useApp((s) => s.toast)
  const doc = useApp((s) => (docId ? s.docs[docId] : undefined))
  const savedPrinter = useApp((s) => s.settings.printer)
  const selectedIds = useMemo(() => (Array.isArray(data?.pages) ? (data.pages as string[]) : null), [data])

  const [printers, setPrinters] = useState<PrinterEntry[] | null>(null)
  const [printer, setPrinter] = useState('')
  const [range, setRange] = useState<Range>(selectedIds ? 'selected' : 'all')
  const [custom, setCustom] = useState('')
  const [subset, setSubset] = useState<Subset>('all')
  const [reverse, setReverse] = useState(false)
  const [copies, setCopies] = useState(1)
  const [color, setColor] = useState<'color' | 'gray'>('color')
  const [duplex, setDuplex] = useState<DuplexMode>('simplex')
  const [layout, setLayout] = useState<PrintLayout>(DEFAULT_LAYOUT)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState('')
  const callId = useRef<number | null>(null)
  const set = (patch: Partial<PrintLayout>): void => setLayout((current) => ({ ...current, ...patch }))

  // Las impresoras se piden a Windows sin bloquear nada: el cuadro ya está abierto.
  useEffect(() => {
    let alive = true
    window.folio.print
      .printers()
      .then(unwrap)
      .then((list) => {
        if (!alive) return
        setPrinters(list)
        const chosen = list.find((p) => p.name === savedPrinter) ?? list.find((p) => p.isDefault) ?? list[0]
        setPrinter(chosen?.name ?? '')
      })
      .catch(() => alive && setPrinters([]))
    return () => {
      alive = false
    }
    // La impresora guardada solo cuenta al abrir el cuadro.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const numberOf = useMemo(() => new Map((doc?.pages ?? []).map((page, index) => [page.id, index + 1])), [doc?.pages])
  const customPages = useMemo(() => (doc && range === 'custom' ? parsePageRange(custom, doc.pages.length) : null), [doc, range, custom])
  const pages = useMemo<PageInfo[]>(() => {
    if (!doc) return []
    let list: PageInfo[]
    if (range === 'current') list = [doc.pages.find((p) => p.id === doc.current) ?? doc.pages[0]].filter(Boolean)
    else if (range === 'selected' && selectedIds) {
      const wanted = new Set(selectedIds)
      list = doc.pages.filter((p) => wanted.has(p.id))
    } else if (range === 'custom') list = customPages ? customPages.map((index) => doc.pages[index]) : []
    else list = doc.pages
    // Impares o pares según su orden en lo que se imprime (para imprimir a dos caras a mano).
    if (subset !== 'all') list = list.filter((_, i) => (i % 2 === 0) === (subset === 'odd'))
    return reverse ? [...list].reverse() : list
  }, [doc, range, selectedIds, customPages, subset, reverse])
  const sheets = useMemo(() => layoutSheets(pages, layout), [pages, layout])

  if (!doc) return null
  const allowed = doc.info.permissions.print
  const busy = progress !== null
  const tooMany = sheets.length > MAX_SHEETS
  const ready = allowed && pages.length > 0 && !busy && !tooMany
  const canPrint = ready && printer !== '' && (printers?.length ?? 0) > 0
  const bookletDuplex = layout.mode === 'booklet' && layout.bookletSides === 'both'
  // En un folleto la doble cara no se elige: a dos caras por el lado corto, o una sola cara.
  const effectiveDuplex: DuplexMode = layout.mode === 'booklet' ? (bookletDuplex ? 'shortEdge' : 'simplex') : duplex
  const twoSided = effectiveDuplex !== 'simplex'
  const paperSheets = Math.ceil(sheets.length / (twoSided ? 2 : 1)) * copies
  const currentNumber = numberOf.get(doc.current) ?? 1

  const T = L.print
  const labelOf = (sheet: Sheet, index: number): string => {
    if (sheet.side) return fmt(sheet.side.back ? T.sheetLabel.back : T.sheetLabel.front, { n: sheet.side.sheet })
    if (sheet.tile) {
      const { row, col } = sheet.tile
      return pages.length > 1
        ? fmt(T.sheetLabel.tileOfPage, { page: numberOf.get(pages[sheet.items[0].page].id) ?? '', row, col })
        : fmt(T.sheetLabel.tile, { row, col })
    }
    if (layout.mode === 'size') return String(numberOf.get(pages[sheet.items[0].page].id) ?? index + 1)
    return fmt(T.sheetLabel.sheet, { n: index + 1 })
  }

  const run = async (mode: PrintRequest['mode']): Promise<void> => {
    setError('')
    setProgress({ done: 0, total: pages.length })
    const tracker = trackProgress((info) => setProgress({ done: info.done, total: info.total }))
    callId.current = tracker.callId
    try {
      const request: PrintRequest =
        mode === 'folio' ? { mode, printer, copies, color: color === 'color', duplex: effectiveDuplex, layout } : { mode, layout }
      unwrap(await window.folio.print.start(doc.id, pages.map((p) => p.id), request, tracker.callId))
      if (mode === 'folio') {
        if (printer !== savedPrinter) void updateSettings({ printer })
        toast('success', fmt(T.sentTo, { printer: printers?.find((p) => p.name === printer)?.label ?? printer }))
      } else {
        toast('success', T.sent)
      }
      close()
    } catch (failure) {
      // Cancelar en el cuadro de Windows deja este abierto, por si se quiere imprimir desde aquí.
      if (!isCancelled(failure)) setError(errorMessage(failure))
      setProgress(null)
    } finally {
      tracker.done()
      callId.current = null
    }
  }

  // Cerrar mientras se prepara cancela la impresión: no sale nada por la impresora.
  const cancel = (): void => {
    if (callId.current !== null) window.folio.cancel(callId.current)
    close()
  }

  const status = progress
    ? progress.done < progress.total
      ? fmt(T.status.preparing, { n: progress.done + 1, total: progress.total })
      : T.status.sending
    : fmt(T.status.summary, { pages: plural(L.common.pageCount, pages.length), sheets: plural(T.sheetCount, paperSheets) })

  return (
    <Dialog
      title={T.print}
      className="print-dialog"
      onClose={cancel}
      footer={
        <>
          <button
            className="btn ghost"
            disabled={!ready}
            data-tip={T.moreSettingsTip}
            onClick={() => void run('system')}
          >
            {T.moreSettings}
          </button>
          <span className="spacer" />
          <span className="print-status" aria-live="polite">
            {progress && (
              <span className="print-bar">
                <span style={{ width: `${Math.round((100 * progress.done) / Math.max(1, progress.total))}%` }} />
              </span>
            )}
            {status}
          </span>
          <button className="btn" onClick={cancel}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={!canPrint} onClick={() => void run('folio')}>
            <Printer size={15} />
            {T.print}
          </button>
        </>
      }
    >
      <div className="print-layout">
        <div className="print-preview" aria-label={T.preview}>
          {sheets.length === 0 ? (
            <span className="print-empty">{T.empty}</span>
          ) : layout.mode === 'poster' ? (
            <PosterPreview doc={doc.id} sheets={sheets} pages={pages} numberOf={numberOf} gray={color === 'gray'} />
          ) : (
            <>
              {sheets.slice(0, PREVIEW_LIMIT).map((sheet, index) => (
                <SheetView key={index} doc={doc.id} sheet={sheet} pages={pages} label={labelOf(sheet, index)} gray={color === 'gray'} />
              ))}
              {sheets.length > PREVIEW_LIMIT && (
                <span className="print-empty">{plural(T.moreSheets, sheets.length - PREVIEW_LIMIT)}</span>
              )}
            </>
          )}
        </div>
        <div className="print-options">
          {!allowed && (
            <div className="notice danger">
              <CircleAlert size={15} />
              <span>{T.notAllowed}</span>
            </div>
          )}
          <PrintGroup title={T.printer.title}>
            <select
              className="select"
              aria-label={T.printer.title}
              title={printers?.find((p) => p.name === printer)?.label}
              value={printer}
              disabled={!printers || printers.length === 0 || busy}
              onChange={(event) => setPrinter(event.target.value)}
            >
              {!printers && <option value="">{T.printer.searching}</option>}
              {printers?.length === 0 && <option value="">{T.printer.none}</option>}
              {printers?.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.isDefault ? fmt(T.printer.isDefault, { name: p.label }) : p.label}
                </option>
              ))}
            </select>
            <div className="print-row print-copies">
              <Field label={T.printer.copies}>
                <NumberField value={copies} onChange={(value) => setCopies(Math.round(value))} min={1} max={99} width={40} tip={T.printer.copies} />
              </Field>
              <Field label={T.printer.color}>
                <Segmented
                  value={color}
                  full
                  options={[
                    { value: 'color', label: T.printer.color },
                    { value: 'gray', label: T.printer.gray }
                  ]}
                  onChange={setColor}
                />
              </Field>
            </div>
          </PrintGroup>
          <PrintGroup title={L.common.pages}>
            <div className="print-ranges">
              <label className="check">
                <input type="radio" name="print-range" checked={range === 'all'} disabled={busy} onChange={() => setRange('all')} />
                <span>{fmt(T.pages.all, { n: doc.pages.length })}</span>
              </label>
              {selectedIds && (
                <label className="check">
                  <input type="radio" name="print-range" checked={range === 'selected'} disabled={busy} onChange={() => setRange('selected')} />
                  <span>{fmt(T.pages.selected, { n: selectedIds.length })}</span>
                </label>
              )}
              <label className="check">
                <input type="radio" name="print-range" checked={range === 'current'} disabled={busy} onChange={() => setRange('current')} />
                <span>{fmt(T.pages.current, { n: currentNumber })}</span>
              </label>
              <label className="check">
                <input type="radio" name="print-range" checked={range === 'custom'} disabled={busy} onChange={() => setRange('custom')} />
                <span>{T.pages.custom}</span>
              </label>
              <input
                className="input"
                aria-label={T.pages.customLabel}
                placeholder={T.pages.customPlaceholder}
                value={custom}
                maxLength={200}
                disabled={busy}
                onFocus={() => setRange('custom')}
                onChange={(event) => {
                  setCustom(event.target.value)
                  setRange('custom')
                }}
              />
              {range === 'custom' && custom.trim() !== '' && !customPages && (
                <span className="error-text">{fmt(T.pages.customError, { count: doc.pages.length })}</span>
              )}
              <div className="print-row">
                <select className="select" aria-label={T.pages.subsetLabel} value={subset} disabled={busy} onChange={(event) => setSubset(event.target.value as Subset)}>
                  <option value="all">{T.pages.both}</option>
                  <option value="odd">{T.pages.odd}</option>
                  <option value="even">{T.pages.even}</option>
                </select>
                <Checkbox checked={reverse} disabled={busy} onChange={setReverse}>
                  {T.pages.reverse}
                </Checkbox>
              </div>
            </div>
          </PrintGroup>
          <PrintGroup title={T.layout.title}>
            {/* Sin etiquetas emergentes: lo que hace cada una se lee en su recuadro
                (y una etiqueta oculta ensancharía el panel, que se desplazaría de lado). */}
            <Segmented
              value={layout.mode}
              full
              options={[
                { value: 'size', label: T.layout.size },
                { value: 'multiple', label: T.layout.multiple },
                { value: 'booklet', label: T.layout.booklet },
                { value: 'poster', label: T.layout.poster }
              ]}
              onChange={(mode) => set(mode !== 'size' && layout.paper === 'document' ? { mode, paper: 'A4' } : { mode })}
            />
            <ModeOptions layout={layout} set={set} busy={busy} />
          </PrintGroup>
          <PrintGroup title={T.paper.title}>
            <div className="print-row">
              <Field label={T.paper.label}>
                <select className="select" aria-label={T.paper.label} value={layout.paper} disabled={busy} onChange={(event) => set({ paper: event.target.value as PaperName })}>
                  {PAPER_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {T.papers[value]}
                    </option>
                  ))}
                  {layout.mode === 'size' && <option value="document">{T.paper.document}</option>}
                </select>
              </Field>
              <Field label={T.paper.orientation}>
                <select
                  className="select"
                  aria-label={T.paper.orientation}
                  value={layout.orientation}
                  disabled={busy || layout.paper === 'document' || layout.mode === 'booklet'}
                  onChange={(event) => set({ orientation: event.target.value as PrintLayout['orientation'] })}
                >
                  <option value="auto">{T.paper.auto}</option>
                  <option value="portrait">{T.paper.portrait}</option>
                  <option value="landscape">{T.paper.landscape}</option>
                </select>
              </Field>
            </div>
            <Field label={T.paper.duplex}>
              <select
                className="select"
                aria-label={T.paper.duplex}
                value={effectiveDuplex}
                disabled={busy || layout.mode === 'booklet'}
                onChange={(event) => setDuplex(event.target.value as DuplexMode)}
              >
                <option value="simplex">{T.paper.simplex}</option>
                <option value="longEdge">{T.paper.longEdge}</option>
                <option value="shortEdge">{T.paper.shortEdge}</option>
              </select>
            </Field>
          </PrintGroup>
          {tooMany && <span className="error-text">{fmt(T.tooMany, { max: new Intl.NumberFormat(locale).format(MAX_SHEETS) })}</span>}
          {error && <span className="error-text">{error}</span>}
        </div>
      </div>
    </Dialog>
  )
}

registerDialog('print', PrintDialog)
