import { Move, Replace, Trash } from 'lucide-react'
import { memo, useState } from 'react'
import type { Color, MutationResult, PageInfo, PlacedObject, Point, Rect, TextStyle } from '@shared/types'
import { L } from '@/i18n'
import { unwrap } from '@/lib/api'
import { usePageData } from '@/lib/pageData'
import { cn } from '@/lib/util'
import { mutate, setTool, toastError } from '@/state/actions'
import { useApp } from '@/state/app'
import { TextEditor, textPadding } from '../common/TextEditor'
import { selectObject } from './objects'

interface EditableLine {
  block: number
  line: number
  rect: Rect
  dir: [number, number]
  text: string
  size: number
  font: string
  bold: boolean
  italic: boolean
  color: Color
  mixed: boolean
}

/** Líneas seguidas que forman un párrafo: se reescribe entero y sus líneas se reparten solas. */
interface EditableParagraph {
  /** Primera línea (identifica el párrafo). */
  block: number
  line: number
  /** Líneas que lo forman ([bloque, línea]). */
  members: [number, number][]
  rect: Rect
  /** Esquina superior izquierda de la caja en la que se escribirá. */
  box: Point
  text: string
  /** Estilo detectado, con su ancho de columna y alineación. */
  style: TextStyle
  mixed: boolean
}

type Editing = { kind: 'line'; line: EditableLine } | { kind: 'paragraph'; paragraph: EditableParagraph }

interface EditableImage {
  xref: number
  index: number
  rect: Rect
}

const warned = new Set<string>()

/**
 * Editar el contenido original elimina de verdad partes del PDF y obliga a
 * reescribirlo, lo que invalida las firmas digitales que tuviera. Se avisa una
 * vez por documento antes del primer cambio.
 */
function guardSigned(doc: string, run: () => void): void {
  const view = useApp.getState().docs[doc]
  if (!view?.state.signed || !view.state.signaturesIntact || warned.has(doc)) {
    run()
    return
  }
  useApp.getState().openDialog({
    type: 'confirm',
    title: L.edit.existing.signedTitle,
    message: L.edit.existing.signedMessage,
    confirmLabel: L.edit.existing.signedConfirm,
    danger: true,
    onConfirm: () => {
      warned.add(doc)
      run()
    }
  })
}

export const ExistingLayer = memo(function ExistingLayer({ doc, page, pxPerPt }: { doc: string; page: PageInfo; pxPerPt: number }) {
  const data = usePageData<{ paragraphs: EditableParagraph[]; lines: EditableLine[]; images: EditableImage[] }>(
    doc,
    page.id,
    page.gen,
    'editables'
  )
  const [editing, setEditing] = useState<Editing | null>(null)
  const [image, setImage] = useState<EditableImage | null>(null)
  if (!data) return null

  const commit = (line: EditableLine, text: string): void => {
    setEditing(null)
    if (text === line.text) return
    guardSigned(doc, () => {
      void mutate<MutationResult & { object?: PlacedObject }>(doc, 'edit.replaceText', {
        page: page.id,
        block: line.block,
        line: line.line,
        rect: line.rect,
        text
      })
    })
  }

  const commitParagraph = (paragraph: EditableParagraph, text: string): void => {
    setEditing(null)
    if (text === paragraph.text) return
    guardSigned(doc, () => {
      void mutate<MutationResult & { object?: PlacedObject }>(doc, 'edit.replaceParagraph', {
        page: page.id,
        block: paragraph.block,
        line: paragraph.line,
        rect: paragraph.rect,
        text
      })
    })
  }

  // Las líneas de un párrafo no se ofrecen sueltas (salvo con Alt+clic sobre el párrafo).
  const inParagraph = new Set(data.paragraphs.flatMap((p) => p.members.map(([b, l]) => `${b}-${l}`)))
  const lineAt = (paragraph: EditableParagraph, x: number, y: number): EditableLine | undefined => {
    const members = new Set(paragraph.members.map(([b, l]) => `${b}-${l}`))
    return data.lines.find(
      (l) => members.has(`${l.block}-${l.line}`) && y >= l.rect[1] && y <= l.rect[3] && x >= l.rect[0] - 2 && x <= l.rect[2] + 2
    )
  }

  const ref = image ? { page: page.id, xref: image.xref, rect: image.rect } : null
  const imageAction = (action: 'delete' | 'detach' | 'replace'): void => {
    if (!ref) return
    setImage(null)
    guardSigned(doc, () => {
      void (async () => {
        if (action === 'delete') {
          await mutate(doc, 'edit.deleteImage', ref)
        } else if (action === 'detach') {
          const result = await mutate<MutationResult & { object?: PlacedObject }>(doc, 'edit.detachImage', ref)
          if (result?.object) {
            setTool(doc, '')
            selectObject(doc, page.id, result.object.id)
          }
        } else {
          try {
            const picked = unwrap(await window.folio.files.pickImage())
            if (picked) await mutate(doc, 'edit.replaceImage', ref, { binary: picked.data })
          } catch (error) {
            toastError(error)
          }
        }
      })()
    })
  }

  let editor = null
  if (editing?.kind === 'paragraph') {
    const target = editing.paragraph
    // El editor ocupa la caja en la que se escribirá el párrafo: mismo margen,
    // misma primera línea base y el mismo ancho, con las líneas repartidas.
    const [x0, y0, x1, y1] = target.rect
    editor = (
      <>
        {/* Mientras se escribe se tapa el original: si el texto nuevo es más corto, no asoma debajo. */}
        <div
          className="edit-mask"
          style={{ left: x0 * pxPerPt - 2, top: y0 * pxPerPt - 2, width: (x1 - x0) * pxPerPt + 4, height: (y1 - y0) * pxPerPt + 4 }}
        />
        <TextEditor
          pxPerPt={pxPerPt}
          x={target.box[0]}
          y={target.box[1]}
          style={target.style}
          initial={target.text}
          onCancel={() => setEditing(null)}
          onCommit={(text) => commitParagraph(target, text)}
        />
      </>
    )
  } else if (editing) {
    const target = editing.line
    const [x0, y0, x1, y1] = target.rect
    const angle = (Math.atan2(target.dir[1], target.dir[0]) * 180) / Math.PI
    const horizontal = Math.abs(target.dir[0]) >= Math.abs(target.dir[1])
    const length = horizontal ? x1 - x0 : y1 - y0
    const thickness = horizontal ? y1 - y0 : x1 - x0
    const style: TextStyle = {
      font: target.font,
      bold: target.bold,
      italic: target.italic,
      size: target.size,
      color: target.color,
      align: 'left',
      lineHeight: 1.2
    }
    const pad = textPadding(target.size)
    const boxHeight = target.size * 1.2 + pad.y * 2
    // La caja del editor se centra sobre la línea original (también si está girada).
    const cx = (x0 + x1) / 2
    const cy = (y0 + y1) / 2
    editor = (
      <TextEditor
        pxPerPt={pxPerPt}
        x={cx - length / 2 - pad.x}
        y={cy - Math.max(boxHeight, thickness) / 2}
        angle={Math.abs(angle) < 0.5 ? 0 : angle}
        style={style}
        initial={target.text}
        minWidth={length + pad.x * 2}
        singleLine
        onCancel={() => setEditing(null)}
        onCommit={(text) => commit(target, text)}
      />
    )
  }

  return (
    <div className="over-layer" onPointerDown={() => setImage(null)}>
      {data.images.map((item) => (
        <div
          key={`i${item.index}`}
          className={cn('editable image', image?.index === item.index && 'selected')}
          style={{
            left: item.rect[0] * pxPerPt,
            top: item.rect[1] * pxPerPt,
            width: (item.rect[2] - item.rect[0]) * pxPerPt,
            height: (item.rect[3] - item.rect[1]) * pxPerPt
          }}
          onPointerDown={(event) => {
            event.stopPropagation()
            setEditing(null)
            setImage(item)
          }}
        />
      ))}
      {data.paragraphs.map((item) =>
        editing?.kind === 'paragraph' && editing.paragraph.block === item.block && editing.paragraph.line === item.line ? null : (
          <div
            key={`p${item.block}-${item.line}`}
            className="editable paragraph"
            title={item.mixed ? L.edit.existing.mixedParagraph : L.edit.existing.paragraphTip}
            style={{
              left: item.rect[0] * pxPerPt,
              top: item.rect[1] * pxPerPt,
              width: (item.rect[2] - item.rect[0]) * pxPerPt,
              height: (item.rect[3] - item.rect[1]) * pxPerPt
            }}
            onPointerDown={(event) => {
              event.stopPropagation()
              // Sin esto, el «mousedown» que sigue lleva el foco a la página y
              // cierra el editor que se acaba de abrir en este mismo clic.
              event.preventDefault()
              setImage(null)
              if (event.altKey) {
                const bounds = event.currentTarget.getBoundingClientRect()
                const x = item.rect[0] + (event.clientX - bounds.left) / pxPerPt
                const y = item.rect[1] + (event.clientY - bounds.top) / pxPerPt
                const single = lineAt(item, x, y)
                if (single) {
                  setEditing({ kind: 'line', line: single })
                  return
                }
              }
              setEditing({ kind: 'paragraph', paragraph: item })
            }}
          />
        )
      )}
      {data.lines.map((line) =>
        inParagraph.has(`${line.block}-${line.line}`) ||
        (editing?.kind === 'line' && editing.line.block === line.block && editing.line.line === line.line) ? null : (
          <div
            key={`t${line.block}-${line.line}`}
            className="editable"
            title={line.mixed ? L.edit.existing.mixedLine : undefined}
            style={{
              left: line.rect[0] * pxPerPt,
              top: line.rect[1] * pxPerPt,
              width: (line.rect[2] - line.rect[0]) * pxPerPt,
              height: (line.rect[3] - line.rect[1]) * pxPerPt
            }}
            onPointerDown={(event) => {
              event.stopPropagation()
              event.preventDefault() // ver el párrafo: si no, el editor se cierra en el mismo clic
              setImage(null)
              setEditing({ kind: 'line', line })
            }}
          />
        )
      )}
      {editor}
      {image && (
        <div
          className="mini-bar"
          style={{ left: ((image.rect[0] + image.rect[2]) / 2) * pxPerPt, top: image.rect[3] * pxPerPt + 8 }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button onClick={() => imageAction('detach')}>
            <Move size={14} />
            {L.edit.existing.moveOrScale}
          </button>
          <button onClick={() => imageAction('replace')}>
            <Replace size={14} />
            {L.edit.existing.replace}
          </button>
          <button className="danger" onClick={() => imageAction('delete')}>
            <Trash size={14} />
            {L.common.delete}
          </button>
        </div>
      )}
    </div>
  )
})
