import { memo } from 'react'
import type { PageInfo, PageLink, Rect } from '@shared/types'
import { L } from '@/i18n'
import { usePageData } from '@/lib/pageData'
import { cn } from '@/lib/util'
import { viewerOf } from '@/lib/viewerControl'
import { useApp } from '@/state/app'
import { ModeLayers } from './ModeLayers'
import { PageCanvas } from './PageCanvas'
import { TextLayer } from './TextLayer'

export interface PageItem {
  id: string
  x: number
  y: number
  w: number
  h: number
}

interface Props {
  doc: string
  page: PageInfo
  item: PageItem
  pxPerPt: number
  priority: number
  view: Rect | null
}

const SearchLayer = memo(function SearchLayer({ doc, page }: { doc: string; page: string }) {
  const indices = useApp((s) => s.docs[doc]?.search.byPage[page])
  const hits = useApp((s) => s.docs[doc]?.search.hits)
  const current = useApp((s) => s.docs[doc]?.search.index ?? -1)
  if (!indices?.length || !hits) return null
  return (
    <>
      {indices.map((index) => {
        const quad = hits[index]?.quad
        if (!quad) return null
        const xs = quad.map((p) => p[0])
        const ys = quad.map((p) => p[1])
        const left = Math.min(...xs)
        const top = Math.min(...ys)
        return (
          <div
            key={index}
            className={cn('hit', index === current && 'current')}
            style={{ left, top, width: Math.max(...xs) - left, height: Math.max(...ys) - top }}
          />
        )
      })}
    </>
  )
})

const LinksLayer = memo(function LinksLayer({ doc, page }: { doc: string; page: PageInfo }) {
  const data = usePageData<{ links: PageLink[] }>(doc, page.id, page.gen, 'links')
  if (!data?.links.length) return null
  return (
    <>
      {data.links.map((link, index) => (
        <div
          key={index}
          className="link"
          role="link"
          title={link.uri ?? L.viewer.goToPage}
          style={{ left: link.r[0], top: link.r[1], width: link.r[2] - link.r[0], height: link.r[3] - link.r[1] }}
          onClick={(event) => {
            event.stopPropagation()
            if (link.page) viewerOf(doc)?.scrollToPage(link.page, link.y)
            // El proceso principal valida el destino y pide confirmación antes de abrirlo.
            else if (link.uri) void window.folio.shell.openExternal(link.uri)
          }}
        />
      ))}
    </>
  )
})

/** Modos en los que el texto de la página se puede seleccionar con el ratón. */
function textSelectable(mode: string, tool: string): boolean {
  if (mode === 'view') return tool !== 'hand'
  if (mode === 'annotate') return tool === '' || ['highlight', 'underline', 'strikeout', 'squiggly'].includes(tool)
  if (mode === 'tools') return tool === 'redact-text'
  return false
}

export const PageView = memo(function PageView({ doc, page, item, pxPerPt, priority, view }: Props) {
  const mode = useApp((s) => s.docs[doc]?.mode ?? 'view')
  const tool = useApp((s) => s.docs[doc]?.tool ?? '')
  const selectable = textSelectable(mode, tool)
  // Al preparar un formulario los campos se muestran como cajas con su nombre,
  // no con el valor que tengan escrito.
  const hideFields = mode === 'form' && (tool === 'design' || tool.startsWith('f-'))
  return (
    <div className="page" data-page={page.id} style={{ left: item.x, top: item.y, width: item.w, height: item.h }}>
      <PageCanvas doc={doc} page={page} pxPerPt={pxPerPt} priority={priority} view={view} annots={!hideFields} />
      <div className="page-units" style={{ width: page.w, height: page.h, transform: `scale(${pxPerPt})` }}>
        {selectable && <TextLayer doc={doc} page={page} />}
        <div className="layer">
          <SearchLayer doc={doc} page={page.id} />
          {mode === 'view' && tool !== 'hand' && <LinksLayer doc={doc} page={page} />}
        </div>
      </div>
      <ModeLayers doc={doc} page={page} pxPerPt={pxPerPt} mode={mode} tool={tool} />
    </div>
  )
})
