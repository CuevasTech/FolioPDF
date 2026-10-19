import { FileCheck2, FileText, FolderOpen, Images, Merge, ScanLine, Search, X } from 'lucide-react'
import { type KeyboardEvent, type ReactNode, useMemo, useRef, useState } from 'react'
import type { RecentFile } from '@shared/types'
import { L, fmt } from '@/i18n'
import { formatWhen } from '@/lib/util'
import { imagesToPdf, mergeFiles, newBlank, openRecent, openWithDialog, updateSettings } from '@/state/actions'
import { useApp } from '@/state/app'
import { chooseFolioAsDefault, useDefaultApp } from '@/state/defaultApp'
import { openScan } from './ScanDialog'
import { Logo } from './ui'


/** Propuesta de abrir los PDF con Folio: se muestra mientras otra aplicación sea la predeterminada y no se haya dicho que no. */
function DefaultAppNotice() {
  const status = useDefaultApp((s) => s.status)
  const ask = useApp((s) => s.settings.askDefaultPdf)
  if (status !== 'other' || !ask) return null
  return (
    <div className="notice start-notice default-app">
      <FileCheck2 size={16} />
      <span>{L.start.defaultApp.text}</span>
      <span className="default-app-actions">
        <button className="btn sm primary" onClick={() => void chooseFolioAsDefault()}>
          {L.start.defaultApp.use}
        </button>
        <button className="btn sm ghost" onClick={() => void updateSettings({ askDefaultPdf: false })}>
          {L.start.defaultApp.dontAsk}
        </button>
      </span>
    </div>
  )
}

// ------------------------------------------------------------- buscador

/**
 * El texto sin tildes ni mayúsculas («camión» se encuentra escribiendo
 * «camion»), con el tramo del original que corresponde a cada letra, para
 * poder resaltar lo encontrado en el nombre tal cual se ve.
 */
function fold(text: string): { folded: string; starts: number[]; ends: number[] } {
  let folded = ''
  const starts: number[] = []
  const ends: number[] = []
  let index = 0
  for (const char of text) {
    const plain = char.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase('es')
    for (let i = 0; i < plain.length; i++) {
      starts.push(index)
      ends.push(index + char.length)
    }
    folded += plain
    index += char.length
  }
  return { folded, starts, ends }
}

const termsOf = (query: string): string[] => fold(query).folded.split(/\s+/).filter(Boolean)

/** Cada palabra buscada tiene que estar en el nombre o en la carpeta. */
function matches(file: RecentFile, terms: string[]): boolean {
  const name = fold(file.name).folded
  const folder = fold(file.folder).folded
  return terms.every((term) => name.includes(term) || folder.includes(term))
}

/** El texto con lo buscado resaltado. */
function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (terms.length === 0) return <>{text}</>
  const { folded, starts, ends } = fold(text)
  const ranges: [number, number][] = []
  for (const term of terms) {
    for (let at = folded.indexOf(term); at >= 0; at = folded.indexOf(term, at + term.length)) {
      ranges.push([starts[at], ends[at + term.length - 1]])
    }
  }
  if (ranges.length === 0) return <>{text}</>
  ranges.sort((a, b) => a[0] - b[0])
  const parts: ReactNode[] = []
  let cursor = 0
  for (const [from, to] of ranges) {
    if (to <= cursor) continue
    const start = Math.max(from, cursor)
    if (start > cursor) parts.push(text.slice(cursor, start))
    parts.push(<mark key={start}>{text.slice(start, to)}</mark>)
    cursor = to
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return <>{parts}</>
}

function RecentList() {
  const recent = useApp((s) => s.recent)
  const setRecent = useApp((s) => s.setRecent)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const terms = useMemo(() => termsOf(query), [query])
  const shown = useMemo(() => (terms.length ? recent.filter((file) => matches(file, terms)) : recent), [recent, terms])

  if (recent.length === 0) return null

  const onSearchKey = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'Escape' && query) {
      event.stopPropagation()
      setQuery('')
    } else if (event.key === 'Enter' && shown[0]) {
      void openRecent(shown[0].id)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      event.currentTarget.closest('.recent')?.querySelector<HTMLElement>('.recent-item')?.focus()
    }
  }

  // Flechas arriba y abajo para recorrer la lista; desde el primero se vuelve al buscador.
  const onListKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('.recent-item'))
    const index = items.indexOf(document.activeElement as HTMLElement)
    if (index < 0) return
    event.preventDefault()
    const next = index + (event.key === 'ArrowDown' ? 1 : -1)
    if (next < 0) searchRef.current?.focus()
    else items[Math.min(next, items.length - 1)]?.focus()
  }

  return (
    <div className="recent">
      <div className="recent-head">
        <span className="recent-title">{terms.length ? fmt(L.start.recent.filtered, { shown: shown.length, total: recent.length }) : L.start.recent.title}</span>
        <label className="recent-search">
          <Search size={14} />
          <input
            ref={searchRef}
            value={query}
            placeholder={L.start.recent.search}
            aria-label={L.start.recent.search}
            spellCheck={false}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onSearchKey}
          />
          {query && (
            <button
              className="icon-btn sm"
              aria-label={L.start.recent.clearSearch}
              onClick={() => {
                setQuery('')
                searchRef.current?.focus()
              }}
            >
              <X size={13} />
            </button>
          )}
        </label>
        <button className="btn ghost sm" onClick={() => void window.folio.recent.clear().then(setRecent)}>
          {L.start.recent.clearList}
        </button>
      </div>
      <div className="recent-list" onKeyDown={onListKey}>
        {shown.map((file) => (
          <div key={file.id} className="recent-row">
            <button className="recent-item" onClick={() => void openRecent(file.id)}>
              <FileText size={18} />
              <div className="grow">
                <div className="recent-name">
                  <Highlight text={file.name} terms={terms} />
                </div>
                <div className="recent-path">
                  <Highlight text={file.folder} terms={terms} />
                </div>
              </div>
              <span className="recent-when">{formatWhen(file.openedAt)}</span>
            </button>
            <button
              className="icon-btn sm recent-remove"
              aria-label={fmt(L.start.recent.removeOne, { name: file.name })}
              data-tip={L.start.recent.removeTip}
              onClick={() => void window.folio.recent.remove(file.id).then(setRecent)}
            >
              <X size={14} />
            </button>
          </div>
        ))}
        {shown.length === 0 && <p className="recent-empty">{fmt(L.start.recent.noMatch, { query: query.trim() })}</p>}
      </div>
    </div>
  )
}

export function StartScreen() {
  return (
    <div className="start">
      <div className="start-inner">
        <div className="start-mark">
          <Logo size={32} />
        </div>
        <h1>Folio</h1>
        <p className="start-sub">{L.start.subtitle}</p>

        <DefaultAppNotice />


        <div className="drop">
          <button className="btn primary lg" onClick={() => void openWithDialog()}>
            <FolderOpen size={17} />
            {L.start.open}
          </button>
          <span className="drop-hint">{L.start.dropHint}</span>
        </div>

        <div className="quick">
          <button onClick={() => void mergeFiles()}>
            <Merge size={18} />
            {L.start.merge}
          </button>
          <button onClick={() => void imagesToPdf()}>
            <Images size={18} />
            {L.start.images}
          </button>
          <button onClick={openScan}>
            <ScanLine size={18} />
            {L.start.scan}
          </button>
          <button onClick={() => void newBlank()}>
            <FileText size={18} />
            {L.start.blank}
          </button>
        </div>

        <RecentList />
      </div>
    </div>
  )
}
