import {
  ArrowDownToLine,
  ArrowUpToLine,
  Circle,
  Eraser,
  Image as ImageIcon,
  Minus,
  MousePointer2,
  Shapes,
  Square,
  SquarePen,
  Trash,
  Type
} from 'lucide-react'
import { useState } from 'react'
import type { MutationResult, PageInfo, PlacedObject, Point, TextStyle } from '@shared/types'
import { ActionButton, ToolButton } from '@/components/Toolbar'
import { MenuButton } from '@/components/ui'
import { L } from '@/i18n'
import { usePageData } from '@/lib/pageData'
import { mutate, setTool } from '@/state/actions'
import { type DocView, useApp } from '@/state/app'
import { ColorButton, NumberField, SubBar, SubLabel } from '../common/controls'
import { DrawLayer } from '../common/DrawLayer'
import { TextEditor, textPadding } from '../common/TextEditor'
import { TextStyleControls } from '../common/TextStyleControls'
import { useTools } from '../common/toolState'
import { ExistingLayer } from './ExistingLayer'
import { addShape, addText, deleteSelection, insertImageFromFile, selectObject } from './objects'
import { ObjectsLayer, styleOf } from './ObjectsLayer'

const SHAPE_TOOLS = ['rect', 'ellipse', 'line']

export function EditTools({ doc }: { doc: DocView }) {
  const shapeActive = SHAPE_TOOLS.includes(doc.tool)
  return (
    <>
      <ToolButton doc={doc} tool="" icon={<MousePointer2 size={16} />} label={L.modes.select} tip={L.edit.tools.selectTip} />
      <div className="sep" />
      <ToolButton doc={doc} tool="text" icon={<Type size={16} />} label={L.edit.tools.text} tip={L.edit.tools.textTip} />
      <ActionButton icon={<ImageIcon size={16} />} label={L.edit.tools.image} tip={L.edit.tools.imageTip} onClick={() => void insertImageFromFile(doc.id)} />
      <MenuButton
        className={shapeActive ? 'tool on' : 'tool'}
        tip={L.edit.tools.shapeTip}
        entries={[
          { label: L.modes.shapes.rect, icon: <Square size={16} />, checked: doc.tool === 'rect', onSelect: () => setTool(doc.id, 'rect') },
          { label: L.modes.shapes.ellipse, icon: <Circle size={16} />, checked: doc.tool === 'ellipse', onSelect: () => setTool(doc.id, 'ellipse') },
          { label: L.modes.shapes.line, icon: <Minus size={16} />, checked: doc.tool === 'line', onSelect: () => setTool(doc.id, 'line') }
        ]}
      >
        <Shapes size={16} />
        <span>{L.modes.shape}</span>
      </MenuButton>
      <ToolButton doc={doc} tool="whiteout" icon={<Eraser size={16} />} label={L.edit.tools.whiteout} tip={L.edit.tools.whiteoutTip} />
      <div className="sep" />
      <ToolButton
        doc={doc}
        tool="edit-content"
        icon={<SquarePen size={16} />}
        label={L.edit.tools.content}
        tip={L.edit.tools.contentTip}
      />
    </>
  )
}

export function EditLayers({ doc, page, pxPerPt, tool }: { doc: string; page: PageInfo; pxPerPt: number; tool: string }) {
  const [adding, setAdding] = useState<Point | null>(null)
  const textStyle = useTools((s) => s.text)
  const shape = useTools((s) => s.shape)
  const hasSelection = useApp((s) => s.docs[doc]?.selection != null)

  if (tool === 'edit-content') return <ExistingLayer doc={doc} page={page} pxPerPt={pxPerPt} />

  if (tool === 'text') {
    const pad = textPadding(textStyle.size)
    return (
      <>
        <DrawLayer pxPerPt={pxPerPt} gesture="point" cursor="text" onPoint={setAdding} />
        {adding && (
          <div className="over-layer" style={{ zIndex: 6 }}>
            <TextEditor
              pxPerPt={pxPerPt}
              // El clic marca el centro de la primera línea de texto.
              x={adding[0] - pad.x}
              y={adding[1] - pad.y - (textStyle.size * textStyle.lineHeight) / 2}
              style={textStyle}
              initial=""
              onCancel={() => setAdding(null)}
              onCommit={(text) => {
                const at: Point = [adding[0] - pad.x, adding[1] - pad.y - (textStyle.size * textStyle.lineHeight) / 2]
                setAdding(null)
                void addText(doc, page.id, at, text, textStyle)
              }}
            />
          </div>
        )}
      </>
    )
  }

  if (tool === 'rect' || tool === 'ellipse') {
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="rect"
        shape={tool}
        onRect={(rect) => void addShape(doc, page.id, tool, rect, { stroke: shape.stroke, fill: shape.fill, width: shape.width })}
      />
    )
  }

  if (tool === 'line') {
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="line"
        width={shape.width}
        onLine={(a, b) => {
          const length = Math.hypot(b[0] - a[0], b[1] - a[1])
          const thickness = Math.max(shape.width * 3, 6)
          const cx = (a[0] + b[0]) / 2
          const cy = (a[1] + b[1]) / 2
          void addShape(doc, page.id, 'line', [cx - length / 2, cy - thickness / 2, cx + length / 2, cy + thickness / 2], {
            stroke: shape.stroke ?? [0, 0, 0],
            fill: null,
            width: shape.width,
            angle: (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI
          })
        }}
      />
    )
  }

  if (tool === 'whiteout') {
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="rect"
        color="#9aa0ab"
        fill="rgba(255,255,255,0.85)"
        onRect={(rect) => void addShape(doc, page.id, 'rect', rect, { stroke: null, fill: [1, 1, 1], width: 0 }, true)}
      />
    )
  }

  return (
    <>
      {hasSelection && <div className="over-layer catch" onPointerDown={() => selectObject(doc, page.id, null)} />}
      <ObjectsLayer doc={doc} page={page} pxPerPt={pxPerPt} />
    </>
  )
}

/** Opciones del elemento seleccionado (compartidas con el modo Firmar). */
export function ObjectOptions({ doc }: { doc: DocView }) {
  const selection = doc.selection?.kind === 'object' && doc.selection.ids.length === 1 ? doc.selection : null
  const page = selection ? doc.pages.find((p) => p.id === selection.page) : undefined
  const data = usePageData<{ objects: PlacedObject[] }>(doc.id, page?.id ?? '', page?.gen ?? 0, 'objects', Boolean(page))
  const object = selection ? data?.objects.find((o) => o.id === selection.ids[0]) : undefined
  const setDefaults = useTools((s) => s.set)
  if (!object || !page) return null

  const base = { page: page.id, id: object.id }
  const common = (
    <>
      <div className="sep" />
      <NumberField
        value={Math.round(object.opacity * 100)}
        min={5}
        max={100}
        step={5}
        suffix="%"
        width={62}
        tip={L.edit.object.opacity}
        onChange={(value) => void mutate(doc.id, 'obj.transform', { ...base, opacity: value / 100 })}
      />
      <button className="icon-btn" data-tip={L.edit.object.bringToFront} onClick={() => void mutate(doc.id, 'obj.order', { ...base, front: true })}>
        <ArrowUpToLine size={15} />
      </button>
      <button className="icon-btn" data-tip={L.edit.object.sendToBack} onClick={() => void mutate(doc.id, 'obj.order', { ...base, front: false })}>
        <ArrowDownToLine size={15} />
      </button>
      <button className="icon-btn" data-tip={L.modes.deleteTip} onClick={() => void deleteSelection(doc.id)}>
        <Trash size={15} />
      </button>
    </>
  )

  if (object.kind === 'text') {
    const style = styleOf(object)
    const apply = (patch: Partial<TextStyle>): void => {
      setDefaults('text', patch)
      void mutate<MutationResult>(doc.id, 'obj.setText', { ...base, style: { ...style, ...patch } })
    }
    return (
      <SubBar>
        <TextStyleControls value={style} onChange={apply} />
        {common}
      </SubBar>
    )
  }

  const shape = String(object.props.shape ?? '')
  if (object.kind === 'shape' && ['rect', 'ellipse', 'line', 'check', 'cross', 'dot'].includes(shape)) {
    const stroke = (object.props.stroke as [number, number, number] | null) ?? null
    const fill = (object.props.fill as [number, number, number] | null) ?? null
    const closed = shape === 'rect' || shape === 'ellipse'
    return (
      <SubBar>
        <SubLabel>{shape === 'dot' ? L.modes.color : L.edit.object.border}</SubLabel>
        <ColorButton
          value={shape === 'dot' ? (fill ?? stroke) : stroke}
          allowNone={closed && fill !== null}
          tip={L.edit.object.strokeColor}
          onChange={(color) =>
            void mutate(doc.id, 'obj.setShape', shape === 'dot' ? { ...base, fill: color } : color ? { ...base, stroke: color } : { ...base, clearStroke: true })
          }
        />
        {closed && (
          <>
            <SubLabel>{L.modes.fill}</SubLabel>
            <ColorButton
              value={fill}
              allowNone={stroke !== null}
              tip={L.edit.object.fillColor}
              onChange={(color) => void mutate(doc.id, 'obj.setShape', color ? { ...base, fill: color } : { ...base, clearFill: true })}
            />
          </>
        )}
        {shape !== 'dot' && (
          <NumberField
            value={Number(object.props.width ?? 1)}
            min={0.25}
            max={40}
            step={0.5}
            suffix="pt"
            width={66}
            tip={L.modes.thickness}
            onChange={(width) => void mutate(doc.id, 'obj.setShape', { ...base, width })}
          />
        )}
        {common}
      </SubBar>
    )
  }

  return <SubBar>{common}</SubBar>
}

export function EditOptions({ doc }: { doc: DocView }) {
  const text = useTools((s) => s.text)
  const shape = useTools((s) => s.shape)
  const setDefaults = useTools((s) => s.set)
  if (doc.mode !== 'edit' || doc.search.open) return null

  if (doc.tool === '' && doc.selection?.kind === 'object') return <ObjectOptions doc={doc} />
  if (doc.tool === 'text') {
    return (
      <SubBar>
        <TextStyleControls value={text} onChange={(patch) => setDefaults('text', patch)} />
      </SubBar>
    )
  }
  if (SHAPE_TOOLS.includes(doc.tool)) {
    const closed = doc.tool !== 'line'
    return (
      <SubBar>
        <SubLabel>{L.edit.object.border}</SubLabel>
        <ColorButton
          value={shape.stroke}
          allowNone={closed && shape.fill !== null}
          onChange={(stroke) => setDefaults('shape', { stroke })}
          tip={L.edit.object.strokeColor}
        />
        {closed && (
          <>
            <SubLabel>{L.modes.fill}</SubLabel>
            <ColorButton value={shape.fill} allowNone={shape.stroke !== null} onChange={(fill) => setDefaults('shape', { fill })} tip={L.edit.object.fillColor} />
          </>
        )}
        <NumberField
          value={shape.width}
          min={0.25}
          max={40}
          step={0.5}
          suffix="pt"
          width={66}
          tip={L.modes.thickness}
          onChange={(width) => setDefaults('shape', { width })}
        />
      </SubBar>
    )
  }
  if (doc.tool === 'edit-content') {
    return (
      <SubBar>
        <SubLabel>{L.edit.hints.content}</SubLabel>
      </SubBar>
    )
  }
  if (doc.tool === 'whiteout') {
    return (
      <SubBar>
        <SubLabel>{L.edit.hints.whiteout}</SubLabel>
      </SubBar>
    )
  }
  return null
}
