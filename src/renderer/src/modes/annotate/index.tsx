import {
  Circle,
  Copy,
  Highlighter,
  Minus,
  MousePointer2,
  MoveUpRight,
  Pencil,
  Shapes,
  Square,
  StickyNote,
  Strikethrough,
  Trash,
  Underline
} from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Color, MutationResult, PageInfo, Point, Rect } from '@shared/types'
import { ToolButton } from '@/components/Toolbar'
import { MenuButton } from '@/components/ui'
import { L } from '@/i18n'
import { usePageData } from '@/lib/pageData'
import { colorToCss, formatDate } from '@/lib/util'
import { mutate, setTool } from '@/state/actions'
import { type DocView, useApp } from '@/state/app'
import type { LayerProps } from '@/viewer/ModeLayers'
import { type Box, BoxLayer } from '../common/BoxLayer'
import { ColorButton, NumberField, SubBar, SubLabel } from '../common/controls'
import { DrawLayer } from '../common/DrawLayer'
import { HIGHLIGHTS, useTools } from '../common/toolState'
import { deleteSelection } from '../edit/objects'
import { readSelection, rectToQuad, type SelectedText } from './selection'

export interface Annot {
  id: string
  kind: string
  rect: Rect
  stroke: Color | null
  fill: Color | null
  width: number
  opacity: number
  contents: string
  author: string
  modified: string | null
  editable: boolean
  quads?: Point[][]
  strokes?: Point[][]
  points?: Point[]
  arrow?: boolean
}

type AnnotResult = MutationResult & { annot?: Annot | null }
type MarkupKind = 'highlight' | 'underline' | 'strikeout' | 'squiggly'

const SHAPES = ['a-rect', 'a-ellipse', 'a-line', 'a-arrow']
const isMarkup = (kind: string): kind is MarkupKind => kind === 'highlight' || kind === 'underline' || kind === 'strikeout' || kind === 'squiggly'

function selectAnnot(doc: string, page: string, id: string | null): void {
  useApp.getState().patchDoc(doc, { selection: id ? { kind: 'annot', page, ids: [id] } : null })
}

/** Marca (resalta, subraya, tacha) el texto seleccionado en todas las páginas que abarque. */
export async function markSelection(doc: string, kind: MarkupKind, selected: SelectedText | null = readSelection(doc)): Promise<void> {
  if (!selected) return
  const color = useTools.getState().markup[kind]
  const author = useApp.getState().settings.author
  window.getSelection()?.removeAllRanges()
  for (const [page, rects] of selected.pages) {
    await mutate(doc, 'annot.addMarkup', { page, kind, quads: rects.map(rectToQuad), color, author })
  }
}

export function AnnotateTools({ doc }: { doc: DocView }) {
  const shapeActive = SHAPES.includes(doc.tool)
  return (
    <>
      <ToolButton doc={doc} tool="" icon={<MousePointer2 size={16} />} label={L.modes.select} tip={L.annotate.tools.selectTip} />
      <div className="sep" />
      <ToolButton doc={doc} tool="highlight" icon={<Highlighter size={16} />} label={L.annotate.tools.highlight} tip={L.annotate.tools.highlightTip} />
      <ToolButton doc={doc} tool="underline" icon={<Underline size={16} />} label={L.annotate.tools.underline} compact />
      <ToolButton doc={doc} tool="strikeout" icon={<Strikethrough size={16} />} label={L.annotate.tools.strikeout} compact />
      <div className="sep" />
      <ToolButton doc={doc} tool="note" icon={<StickyNote size={16} />} label={L.annotate.tools.note} tip={L.annotate.tools.noteTip} />
      <ToolButton doc={doc} tool="ink" icon={<Pencil size={16} />} label={L.annotate.tools.ink} tip={L.annotate.tools.inkTip} />
      <MenuButton
        className={shapeActive ? 'tool on' : 'tool'}
        tip={L.annotate.tools.shapesTip}
        entries={[
          { label: L.modes.shapes.rect, icon: <Square size={16} />, checked: doc.tool === 'a-rect', onSelect: () => setTool(doc.id, 'a-rect') },
          { label: L.modes.shapes.ellipse, icon: <Circle size={16} />, checked: doc.tool === 'a-ellipse', onSelect: () => setTool(doc.id, 'a-ellipse') },
          { label: L.modes.shapes.line, icon: <Minus size={16} />, checked: doc.tool === 'a-line', onSelect: () => setTool(doc.id, 'a-line') },
          { label: L.annotate.tools.arrow, icon: <MoveUpRight size={16} />, checked: doc.tool === 'a-arrow', onSelect: () => setTool(doc.id, 'a-arrow') }
        ]}
      >
        <Shapes size={16} />
        <span>{L.modes.shape}</span>
      </MenuButton>
    </>
  )
}

/** Comentario de la anotación seleccionada. */
function CommentBox({ doc, page, annot, pxPerPt, pageWidth }: { doc: string; page: string; annot: Annot; pxPerPt: number; pageWidth: number }) {
  const [text, setText] = useState(annot.contents)
  const latest = useRef({ text: annot.contents, saved: annot.contents })
  latest.current.text = text
  useEffect(() => {
    setText(annot.contents)
    latest.current = { text: annot.contents, saved: annot.contents }
  }, [annot.id, annot.contents])
  const save = useCallback((): void => {
    const state = latest.current
    if (state.text === state.saved) return
    state.saved = state.text
    void mutate(doc, 'annot.update', { page, id: annot.id, contents: state.text })
  }, [doc, page, annot.id])
  // Si el cuadro se cierra al hacer clic fuera, el comentario se guarda igualmente.
  useEffect(() => save, [save])
  return (
    <div
      className="note-pop"
      // Siempre dentro de la página, aunque la anotación esté pegada al borde derecho.
      style={{ left: Math.max(4, Math.min(annot.rect[0] * pxPerPt, pageWidth * pxPerPt - 252)), top: annot.rect[3] * pxPerPt + 10 }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <textarea
        className="textarea"
        rows={3}
        placeholder={L.annotate.comment.placeholder}
        value={text}
        data-autofocus={annot.kind === 'note' && !annot.contents ? '' : undefined}
        autoFocus={annot.kind === 'note' && !annot.contents}
        onChange={(event) => setText(event.target.value)}
        onBlur={save}
        onKeyDown={(event) => {
          event.stopPropagation()
          if (event.key === 'Escape' || (event.key === 'Enter' && (event.ctrlKey || event.metaKey))) event.currentTarget.blur()
        }}
      />
      <div className="meta">
        <span>{annot.author || L.annotate.comment.noAuthor}</span>
        <span>{annot.modified ? formatDate(annot.modified) : ''}</span>
      </div>
    </div>
  )
}

const AnnotsLayer = memo(function AnnotsLayer({ doc, page, pxPerPt }: { doc: string; page: PageInfo; pxPerPt: number }) {
  const data = usePageData<{ annots: Annot[] }>(doc, page.id, page.gen, 'annots')
  const selection = useApp((s) => s.docs[doc]?.selection)
  const annots = useMemo(() => (data?.annots ?? []).filter((a) => a.editable), [data])
  const selected = useMemo(
    () => (selection?.kind === 'annot' && selection.page === page.id ? selection.ids : []),
    [selection, page.id]
  )
  const boxes = useMemo<Box[]>(
    () =>
      annots.map((a) => {
        const [x0, y0, x1, y1] = a.rect
        const shape = a.kind === 'rect' || a.kind === 'ellipse'
        return {
          id: a.id,
          cx: (x0 + x1) / 2,
          cy: (y0 + y1) / 2,
          w: Math.max(x1 - x0, 6),
          h: Math.max(y1 - y0, 6),
          angle: 0,
          resize: shape,
          movable: !isMarkup(a.kind),
          className: isMarkup(a.kind) ? 'static' : Math.min(x1 - x0, y1 - y0) * pxPerPt < 26 ? 'small' : undefined
        }
      }),
    [annots, pxPerPt]
  )

  const onSelect = useCallback((id: string) => selectAnnot(doc, page.id, id), [doc, page.id])
  const onChange = useCallback(
    (id: string, next: Box, change: string) => {
      const annot = annots.find((a) => a.id === id)
      if (!annot) return
      const [x0, y0, x1, y1] = annot.rect
      void (async () => {
        let result: AnnotResult | null
        if (change === 'resize') {
          result = await mutate<AnnotResult>(doc, 'annot.update', {
            page: page.id,
            id,
            rect: [next.cx - next.w / 2, next.cy - next.h / 2, next.cx + next.w / 2, next.cy + next.h / 2]
          })
        } else {
          result = await mutate<AnnotResult>(doc, 'annot.update', {
            page: page.id,
            id,
            move: [next.cx - (x0 + x1) / 2, next.cy - (y0 + y1) / 2]
          })
        }
        // Dibujos y líneas se recrean al moverlos: la selección pasa al nuevo.
        if (result?.annot && result.annot.id !== id) selectAnnot(doc, page.id, result.annot.id)
      })()
    },
    [annots, doc, page.id]
  )

  const current = selected.length === 1 ? annots.find((a) => a.id === selected[0]) : undefined
  return (
    <>
      {selected.length > 0 && <div className="over-layer catch" onPointerDown={() => selectAnnot(doc, page.id, null)} />}
      <BoxLayer boxes={boxes} pxPerPt={pxPerPt} selected={selected} onSelect={onSelect} onChange={onChange} />
      {current && (
        <div className="over-layer">
          <CommentBox doc={doc} page={page.id} annot={current} pxPerPt={pxPerPt} pageWidth={page.w} />
        </div>
      )}
    </>
  )
})

export function AnnotateLayers({ doc, page, pxPerPt, tool }: LayerProps) {
  const draw = useTools((s) => s.draw)
  const note = useTools((s) => s.note)
  const author = useApp((s) => s.settings.author)
  const common = { page: page.id, author }

  if (tool === 'note') {
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="point"
        cursor="copy"
        onPoint={(point) =>
          void (async () => {
            const result = await mutate<AnnotResult>(doc, 'annot.add', { ...common, kind: 'note', point, stroke: note.color })
            if (result?.annot) {
              setTool(doc, '')
              selectAnnot(doc, page.id, result.annot.id)
            }
          })()
        }
      />
    )
  }
  if (tool === 'ink') {
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="ink"
        color={colorToCss(draw.color)}
        width={draw.width}
        onInk={(stroke) =>
          void mutate(doc, 'annot.add', { ...common, kind: 'ink', strokes: [stroke], stroke: draw.color, width: draw.width, opacity: draw.opacity })
        }
      />
    )
  }
  if (tool === 'a-rect' || tool === 'a-ellipse') {
    const kind = tool === 'a-rect' ? 'rect' : 'ellipse'
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="rect"
        shape={kind}
        color={colorToCss(draw.color)}
        onRect={(rect) =>
          void (async () => {
            const result = await mutate<AnnotResult>(doc, 'annot.add', { ...common, kind, rect, stroke: draw.color, width: draw.width })
            if (result?.annot) {
              setTool(doc, '')
              selectAnnot(doc, page.id, result.annot.id)
            }
          })()
        }
      />
    )
  }
  if (tool === 'a-line' || tool === 'a-arrow') {
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="line"
        color={colorToCss(draw.color)}
        width={draw.width}
        onLine={(a, b) =>
          void (async () => {
            const result = await mutate<AnnotResult>(doc, 'annot.add', {
              ...common,
              kind: tool === 'a-arrow' ? 'arrow' : 'line',
              points: [a, b],
              stroke: draw.color,
              width: draw.width
            })
            if (result?.annot) {
              setTool(doc, '')
              selectAnnot(doc, page.id, result.annot.id)
            }
          })()
        }
      />
    )
  }
  // Con las herramientas de marcado se selecciona texto; las anotaciones solo se manejan con el puntero.
  if (tool === '') return <AnnotsLayer doc={doc} page={page} pxPerPt={pxPerPt} />
  return null
}

/** Menú flotante al seleccionar texto (modos Ver y Anotar) y marcado directo con las herramientas de texto. */
export function SelectionActions({ doc }: { doc: DocView }) {
  const [selected, setSelected] = useState<SelectedText | null>(null)
  const direct = doc.mode === 'annotate' && isMarkup(doc.tool) ? doc.tool : null
  const enabled = (doc.mode === 'view' && doc.tool === '') || (doc.mode === 'annotate' && doc.tool === '') || direct !== null

  useEffect(() => {
    if (!enabled) {
      setSelected(null)
      return
    }
    const onUp = (event: PointerEvent): void => {
      if ((event.target as HTMLElement | null)?.closest('.selection-bar')) return
      // Se espera a que el navegador termine de actualizar la selección.
      window.setTimeout(() => {
        const current = readSelection(doc.id)
        if (direct && current) {
          void markSelection(doc.id, direct, current)
          setSelected(null)
        } else {
          setSelected(current)
        }
      }, 0)
    }
    const onDown = (event: PointerEvent): void => {
      if (!(event.target as HTMLElement | null)?.closest('.selection-bar')) setSelected(null)
    }
    const viewer = document.querySelector<HTMLElement>(`.viewer[data-doc="${doc.id}"]`)
    if (!viewer) return
    viewer.addEventListener('pointerup', onUp)
    viewer.addEventListener('pointerdown', onDown)
    const onScroll = (): void => setSelected(null)
    viewer.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      viewer.removeEventListener('pointerup', onUp)
      viewer.removeEventListener('pointerdown', onDown)
      viewer.removeEventListener('scroll', onScroll)
    }
  }, [doc.id, enabled, direct])

  if (!selected || direct) return null
  const mark = (kind: MarkupKind): void => {
    void markSelection(doc.id, kind, selected)
    setSelected(null)
  }
  const canAnnotate = doc.info.permissions.annotate
  return (
    <div
      className="selection-bar"
      style={{ left: Math.min(selected.anchor.x + 6, window.innerWidth - 300), top: Math.min(selected.anchor.y + 8, window.innerHeight - 50) }}
    >
      <button
        onClick={() => {
          void navigator.clipboard.writeText(selected.text)
          setSelected(null)
          useApp.getState().toast('success', L.annotate.selection.copied)
        }}
      >
        <Copy size={14} />
        {L.annotate.selection.copy}
      </button>
      {canAnnotate && (
        <>
          <button onClick={() => mark('highlight')}>
            <span className="swatch" style={{ background: colorToCss(useTools.getState().markup.highlight) }} />
            {L.annotate.tools.highlight}
          </button>
          <button onClick={() => mark('underline')} title={L.annotate.tools.underline}>
            <Underline size={14} />
          </button>
          <button onClick={() => mark('strikeout')} title={L.annotate.tools.strikeout}>
            <Strikethrough size={14} />
          </button>
        </>
      )}
    </div>
  )
}

export function AnnotateOptions({ doc }: { doc: DocView }) {
  const tools = useTools()
  const selection = doc.selection?.kind === 'annot' && doc.selection.ids.length === 1 ? doc.selection : null
  const page = selection ? doc.pages.find((p) => p.id === selection.page) : undefined
  const data = usePageData<{ annots: Annot[] }>(doc.id, page?.id ?? '', page?.gen ?? 0, 'annots', Boolean(page))
  if (doc.mode !== 'annotate' || doc.search.open) return null

  const annot = selection ? data?.annots.find((a) => a.id === selection.ids[0]) : undefined
  if (doc.tool === '' && annot && page) {
    const update = (patch: Record<string, unknown>): void => void mutate(doc.id, 'annot.update', { page: page.id, id: annot.id, ...patch })
    const shape = annot.kind === 'rect' || annot.kind === 'ellipse'
    return (
      <SubBar>
        <SubLabel>{L.modes.color}</SubLabel>
        <ColorButton
          value={annot.stroke}
          palette={isMarkup(annot.kind) || annot.kind === 'note' ? [...HIGHLIGHTS, [0.86, 0.2, 0.22], [0.15, 0.6, 0.3]] : undefined}
          onChange={(color) => color && update({ stroke: color })}
        />
        {shape && (
          <>
            <SubLabel>{L.modes.fill}</SubLabel>
            <ColorButton value={annot.fill} allowNone onChange={(color) => update(color ? { fill: color } : { clearFill: true })} />
          </>
        )}
        {!isMarkup(annot.kind) && annot.kind !== 'note' && (
          <NumberField value={annot.width} min={0.25} max={30} step={0.5} suffix="pt" width={66} tip={L.modes.thickness} onChange={(width) => update({ width })} />
        )}
        <div className="sep" />
        <button className="icon-btn" data-tip={L.modes.deleteTip} onClick={() => void deleteSelection(doc.id)}>
          <Trash size={15} />
        </button>
      </SubBar>
    )
  }
  if (isMarkup(doc.tool)) {
    const kind = doc.tool
    return (
      <SubBar>
        <SubLabel>{L.annotate.options.markupHint}</SubLabel>
        <ColorButton
          value={tools.markup[kind]}
          palette={kind === 'highlight' ? HIGHLIGHTS : undefined}
          onChange={(color) => color && tools.set('markup', { [kind]: color })}
        />
      </SubBar>
    )
  }
  if (doc.tool === 'ink' || SHAPES.includes(doc.tool)) {
    return (
      <SubBar>
        <SubLabel>{L.annotate.options.stroke}</SubLabel>
        <ColorButton value={tools.draw.color} onChange={(color) => color && tools.set('draw', { color })} />
        <NumberField
          value={tools.draw.width}
          min={0.5}
          max={30}
          step={0.5}
          suffix="pt"
          width={66}
          tip={L.modes.thickness}
          onChange={(width) => tools.set('draw', { width })}
        />
      </SubBar>
    )
  }
  if (doc.tool === 'note') {
    return (
      <SubBar>
        <SubLabel>{L.annotate.options.noteHint}</SubLabel>
      </SubBar>
    )
  }
  return null
}
