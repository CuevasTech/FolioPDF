import { ChevronDown, ChevronUp, Minus, PanelLeft, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { L, fmt } from '@/i18n'
import { cn } from '@/lib/util'
import { viewerOf } from '@/lib/viewerControl'
import { type DocView, useApp, type ZoomKind } from '@/state/app'
import { MenuButton, type MenuEntry } from './ui'

const PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4]

/** Indicador flotante de página y zoom. */
export function Dock({ doc }: { doc: DocView }) {
  const patchDoc = useApp((s) => s.patchDoc)
  const index = Math.max(0, doc.pages.findIndex((p) => p.id === doc.current))
  const [draft, setDraft] = useState(String(index + 1))

  useEffect(() => setDraft(String(index + 1)), [index])

  const goTo = (position: number): void => {
    const target = doc.pages[Math.min(doc.pages.length - 1, Math.max(0, position))]
    if (target) viewerOf(doc.id)?.scrollToPage(target.id)
  }

  const setZoom = (kind: ZoomKind, scale = doc.zoom.scale): void => patchDoc(doc.id, { zoom: { kind, scale } })

  const zoomMenu: MenuEntry[] = [
    { label: L.shell.dock.fitWidth, checked: doc.zoom.kind === 'fit-width', onSelect: () => setZoom('fit-width') },
    { label: L.shell.dock.fitPage, checked: doc.zoom.kind === 'fit-page', onSelect: () => setZoom('fit-page') },
    'sep',
    ...PRESETS.map(
      (scale): MenuEntry => ({
        label: fmt(L.shell.percent, { n: Math.round(scale * 100) }),
        checked: doc.zoom.kind === 'custom' && Math.abs(doc.zoom.scale - scale) < 0.005,
        onSelect: () => setZoom('custom', scale)
      })
    )
  ]

  return (
    <div className="dock">
      <button
        className={cn('icon-btn', doc.side && 'on')}
        data-tip={L.shell.dock.sidePanel}
        data-tip-side="top"
        onClick={() => patchDoc(doc.id, { side: doc.side ? null : 'thumbs' })}
      >
        <PanelLeft size={15} />
      </button>
      <div className="sep" />
      <button className="icon-btn" data-tip={L.shell.dock.previousPage} data-tip-side="top" disabled={index <= 0} onClick={() => goTo(index - 1)}>
        <ChevronUp size={15} />
      </button>
      <div className="dock-page">
        <input
          value={draft}
          inputMode="numeric"
          aria-label={L.common.page}
          onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))}
          onFocus={(event) => event.target.select()}
          onBlur={() => setDraft(String(index + 1))}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              const value = parseInt(draft, 10)
              if (Number.isFinite(value)) goTo(value - 1)
              event.currentTarget.blur()
            } else if (event.key === 'Escape') {
              event.currentTarget.blur()
            }
          }}
        />
        <span>{fmt(L.shell.dock.ofTotal, { total: doc.pages.length })}</span>
      </div>
      <button
        className="icon-btn"
        data-tip={L.shell.dock.nextPage}
        data-tip-side="top"
        disabled={index >= doc.pages.length - 1}
        onClick={() => goTo(index + 1)}
      >
        <ChevronDown size={15} />
      </button>
      <div className="sep" />
      <button className="icon-btn" data-tip={L.shell.dock.zoomOut} data-tip-side="top" onClick={() => viewerOf(doc.id)?.zoomStep(-1)}>
        <Minus size={15} />
      </button>
      <MenuButton className="dock-zoom" entries={zoomMenu} side="top" align="start">
        {fmt(L.shell.percent, { n: Math.round(doc.zoom.scale * 100) })}
      </MenuButton>
      <button className="icon-btn" data-tip={L.shell.dock.zoomIn} data-tip-side="top" onClick={() => viewerOf(doc.id)?.zoomStep(1)}>
        <Plus size={15} />
      </button>
    </div>
  )
}
