import { ArrowDownToLine, ArrowUpToLine, Trash } from 'lucide-react'
import { memo, useCallback, useMemo, useState } from 'react'
import type { MutationResult, ObjectKind, PageInfo, PlacedObject, TextStyle } from '@shared/types'
import { Menu, type MenuEntry } from '@/components/ui'
import { L } from '@/i18n'
import { usePageData } from '@/lib/pageData'
import { mutate } from '@/state/actions'
import { useApp } from '@/state/app'
import { type Box, BoxLayer } from '../common/BoxLayer'
import { TextEditor } from '../common/TextEditor'
import { selectObject } from './objects'

const VISIBLE: ObjectKind[] = ['text', 'image', 'signature', 'shape', 'stamp']
const FREE_RESIZE = new Set(['rect', 'ellipse', 'line'])

export function styleOf(object: PlacedObject): TextStyle {
  const p = object.props as Partial<TextStyle>
  return {
    font: typeof p.font === 'string' ? p.font : 'Arial',
    bold: p.bold === true,
    italic: p.italic === true,
    size: typeof p.size === 'number' ? p.size : 12,
    color: Array.isArray(p.color) && p.color.length === 3 ? p.color : [0, 0, 0],
    align: p.align === 'center' || p.align === 'right' || p.align === 'justify' ? p.align : 'left',
    lineHeight: typeof p.lineHeight === 'number' ? p.lineHeight : 1.2,
    // Un párrafo editado conserva su ancho: al cambiar el texto o el estilo se vuelve a repartir.
    ...(typeof p.width === 'number' && p.width > 0 ? { width: p.width } : {})
  }
}

interface Props {
  doc: string
  page: PageInfo
  pxPerPt: number
}

/** Elementos añadidos con Folio (texto, imágenes, firmas, formas): seleccionar, mover, escalar, girar y editar. */
export const ObjectsLayer = memo(function ObjectsLayer({ doc, page, pxPerPt }: Props) {
  const data = usePageData<{ objects: PlacedObject[] }>(doc, page.id, page.gen, 'objects')
  const selection = useApp((s) => s.docs[doc]?.selection)
  const [editing, setEditing] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null)

  const objects = useMemo(() => (data?.objects ?? []).filter((o) => VISIBLE.includes(o.kind)), [data])
  const selected = useMemo(
    () => (selection?.kind === 'object' && selection.page === page.id ? selection.ids : []),
    [selection, page.id]
  )
  const boxes = useMemo<Box[]>(
    () =>
      objects
        .filter((o) => o.id !== editing)
        .map((o) => ({
          id: o.id,
          cx: o.cx,
          cy: o.cy,
          w: o.w,
          h: o.h,
          angle: o.angle,
          rotate: true,
          resize: o.kind === 'shape' && FREE_RESIZE.has(String(o.props.shape)) ? true : 'aspect',
          className: Math.min(o.w, o.h) * pxPerPt < 26 ? 'small' : undefined
        })),
    [objects, editing, pxPerPt]
  )

  const onSelect = useCallback((id: string) => selectObject(doc, page.id, id), [doc, page.id])
  const onChange = useCallback(
    (id: string, next: Box) => {
      void mutate(doc, 'obj.transform', { page: page.id, id, cx: next.cx, cy: next.cy, w: next.w, h: next.h, angle: next.angle })
    },
    [doc, page.id]
  )
  const onActivate = useCallback(
    (id: string) => {
      if (objects.find((o) => o.id === id)?.kind === 'text') setEditing(id)
    },
    [objects]
  )

  const target = editing ? objects.find((o) => o.id === editing) : undefined
  const menuEntries = (id: string): MenuEntry[] => [
    {
      label: L.edit.object.bringToFront,
      icon: <ArrowUpToLine size={16} />,
      onSelect: () => void mutate(doc, 'obj.order', { page: page.id, id, front: true })
    },
    {
      label: L.edit.object.sendToBack,
      icon: <ArrowDownToLine size={16} />,
      onSelect: () => void mutate(doc, 'obj.order', { page: page.id, id, front: false })
    },
    'sep',
    {
      label: L.common.delete,
      icon: <Trash size={16} />,
      danger: true,
      kbd: L.modes.deleteKey,
      onSelect: () => {
        selectObject(doc, page.id, null)
        void mutate(doc, 'obj.delete', { page: page.id, ids: [id] })
      }
    }
  ]

  if (objects.length === 0) return null
  return (
    <>
      <BoxLayer
        boxes={boxes}
        pxPerPt={pxPerPt}
        selected={selected}
        onSelect={onSelect}
        onChange={onChange}
        onActivate={onActivate}
        onContext={(id, event) => setMenu({ id, x: event.clientX, y: event.clientY })}
      />
      {target && (
        <div className="over-layer">
          <TextEditor
            pxPerPt={pxPerPt}
            x={target.cx - target.w / 2}
            y={target.cy - target.h / 2}
            angle={target.angle}
            style={styleOf(target)}
            initial={String(target.props.text ?? '')}
            onCancel={() => setEditing(null)}
            onCommit={(text) => {
              setEditing(null)
              if (!text.trim()) {
                selectObject(doc, page.id, null)
                void mutate(doc, 'obj.delete', { page: page.id, ids: [target.id] })
              } else if (text !== target.props.text) {
                void mutate<MutationResult>(doc, 'obj.setText', { page: page.id, id: target.id, text })
              }
            }}
          />
        </div>
      )}
      {menu && <Menu anchor={{ x: menu.x, y: menu.y }} entries={menuEntries(menu.id)} onClose={() => setMenu(null)} />}
    </>
  )
})
