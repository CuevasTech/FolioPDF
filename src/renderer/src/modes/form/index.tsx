import {
  ChevronsUpDown,
  CircleDot,
  Copy,
  Eraser,
  List,
  MousePointer2,
  Signature,
  SquareCheck,
  TextCursorInput,
  Trash,
  WandSparkles,
  X
} from 'lucide-react'
import { memo, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { create } from 'zustand'
import type { Color, MutationResult, Rect } from '@shared/types'
import { ActionButton, ToolButton } from '@/components/Toolbar'
import { Checkbox, Field, Segmented } from '@/components/ui'
import { fmt, L, plural } from '@/i18n'
import { query } from '@/lib/api'
import { usePageData } from '@/lib/pageData'
import { mutate, setTool, toastError, updateSettings } from '@/state/actions'
import { type DocView, useApp } from '@/state/app'
import type { LayerProps } from '@/viewer/ModeLayers'
import { type Box, BoxLayer } from '../common/BoxLayer'
import { ColorButton, NumberField, SubBar, SubLabel } from '../common/controls'
import { DrawLayer } from '../common/DrawLayer'
import { deleteSelection } from '../edit/objects'
import { FillLayer, type Widget } from './FillLayer'

type FieldsResult = MutationResult & { widgets?: Widget[]; widget?: Widget }

interface Suggestion {
  type: 'text' | 'checkbox'
  rect: Rect
  label: string
  multiline: boolean
}

/** Campos propuestos por la detección automática, por documento y página. */
const useSuggestions = create<{
  items: Record<string, Suggestion[]>
  set(key: string, list: Suggestion[]): void
  clear(doc: string): void
}>()((set) => ({
  items: {},
  set: (key, list) => set((s) => ({ items: { ...s.items, [key]: list } })),
  clear: (doc) => set((s) => ({ items: Object.fromEntries(Object.entries(s.items).filter(([key]) => !key.startsWith(`${doc}|`))) }))
}))

const CREATE: Record<string, { type: string; w: number; h: number; label: string; tip: string; icon: ReactNode }> = {
  'f-text': { type: 'text', w: 180, h: 22, ...L.form.create.text, icon: <TextCursorInput size={16} /> },
  'f-check': { type: 'checkbox', w: 14, h: 14, ...L.form.create.checkbox, icon: <SquareCheck size={16} /> },
  'f-radio': { type: 'radio', w: 14, h: 14, ...L.form.create.radio, icon: <CircleDot size={16} /> },
  'f-combo': { type: 'combo', w: 160, h: 22, ...L.form.create.combo, icon: <ChevronsUpDown size={16} /> },
  'f-list': { type: 'list', w: 160, h: 64, ...L.form.create.list, icon: <List size={16} /> },
  'f-sign': { type: 'signature', w: 190, h: 64, ...L.form.create.signature, icon: <Signature size={16} /> }
}
const ICONS: Record<string, ReactNode> = {
  text: <TextCursorInput size={11} />,
  checkbox: <SquareCheck size={11} />,
  radio: <CircleDot size={11} />,
  combo: <ChevronsUpDown size={11} />,
  list: <List size={11} />,
  signature: <Signature size={11} />
}

const designing = (tool: string): boolean => tool === 'design' || tool.startsWith('f-')

function selectField(doc: string, page: string, id: string | null): void {
  useApp.getState().patchDoc(doc, { selection: id ? { kind: 'widget', page, ids: [id] } : null })
}

async function detectFields(doc: DocView): Promise<void> {
  try {
    const { suggestions } = await query<{ suggestions: Suggestion[] }>('form.autoDetect', { doc: doc.id, page: doc.current })
    useSuggestions.getState().set(`${doc.id}|${doc.current}`, suggestions)
    useApp
      .getState()
      .toast(suggestions.length ? 'info' : 'warning', suggestions.length ? plural(L.form.detect.found, suggestions.length) : L.form.detect.none)
  } catch (error) {
    toastError(error)
  }
}

async function acceptSuggestions(doc: string, page: string, list: Suggestion[]): Promise<void> {
  if (list.length === 0) return
  const fields = list.map((s) => ({ type: s.type, rect: s.rect, multiline: s.multiline, name: s.label || undefined, label: s.label || undefined }))
  const result = await mutate<FieldsResult>(doc, 'form.addFields', { page, fields })
  if (result) {
    const key = `${doc}|${page}`
    const rest = (useSuggestions.getState().items[key] ?? []).filter((s) => !list.includes(s))
    useSuggestions.getState().set(key, rest)
  }
}

export function FormTools({ doc }: { doc: DocView }) {
  const highlight = useApp((s) => s.settings.highlightFields)
  const design = designing(doc.tool)
  return (
    <>
      <Segmented
        value={design ? 'design' : 'fill'}
        options={[
          { value: 'fill', label: L.form.tools.fill },
          { value: 'design', label: L.form.tools.design }
        ]}
        onChange={(value) => setTool(doc.id, value === 'design' ? 'design' : '')}
      />
      <div className="sep" />
      {design ? (
        <>
          <ToolButton doc={doc} tool="design" icon={<MousePointer2 size={16} />} label={L.form.tools.select} compact />
          {Object.entries(CREATE).map(([tool, spec]) => (
            <ToolButton key={tool} doc={doc} tool={tool} icon={spec.icon} label={spec.label} tip={spec.tip} compact />
          ))}
          <div className="sep" />
          <ActionButton
            icon={<WandSparkles size={16} />}
            label={L.form.tools.detect}
            tip={L.form.tools.detectTip}
            onClick={() => void detectFields(doc)}
          />
        </>
      ) : (
        <>
          <ActionButton
            icon={<SquareCheck size={16} />}
            label={L.form.tools.highlight}
            active={highlight}
            onClick={() => void updateSettings({ highlightFields: !highlight })}
          />
          <ActionButton
            icon={<Eraser size={16} />}
            label={L.form.tools.clear}
            tip={L.form.tools.clearTip}
            disabled={!doc.info.hasForm}
            onClick={() =>
              useApp.getState().openDialog({
                type: 'confirm',
                title: L.form.tools.clearTitle,
                message: L.form.tools.clearMessage,
                confirmLabel: L.form.tools.clear,
                onConfirm: () => void mutate(doc.id, 'form.reset')
              })
            }
          />
        </>
      )}
    </>
  )
}

const DesignLayer = memo(function DesignLayer({ doc, page, pxPerPt, tool }: LayerProps) {
  const data = usePageData<{ widgets: Widget[] }>(doc, page.id, page.gen, 'widgets')
  const selection = useApp((s) => s.docs[doc]?.selection)
  const suggestions = useSuggestions((s) => s.items[`${doc}|${page.id}`])
  const widgets = useMemo(() => (data?.widgets ?? []).filter((w) => w.type !== 'other' && w.type !== 'button'), [data])
  const selected = useMemo(
    () => (selection?.kind === 'widget' && selection.page === page.id ? selection.ids : []),
    [selection, page.id]
  )
  const boxes = useMemo<Box[]>(
    () =>
      widgets.map((w) => ({
        id: w.id,
        cx: (w.rect[0] + w.rect[2]) / 2,
        cy: (w.rect[1] + w.rect[3]) / 2,
        w: w.rect[2] - w.rect[0],
        h: w.rect[3] - w.rect[1],
        angle: 0,
        resize: !(w.type === 'signature' && w.signed),
        movable: !(w.type === 'signature' && w.signed),
        className: Math.min(w.rect[2] - w.rect[0], w.rect[3] - w.rect[1]) * pxPerPt < 26 ? 'small' : undefined
      })),
    [widgets, pxPerPt]
  )
  const onSelect = useCallback((id: string) => selectField(doc, page.id, id), [doc, page.id])
  const onChange = useCallback(
    (id: string, next: Box) => {
      void mutate(doc, 'form.updateField', {
        page: page.id,
        id,
        rect: [next.cx - next.w / 2, next.cy - next.h / 2, next.cx + next.w / 2, next.cy + next.h / 2]
      })
    },
    [doc, page.id]
  )

  const spec = CREATE[tool]
  const create = async (rect: Rect): Promise<void> => {
    if (!spec) return
    let id: string | undefined
    if (spec.type === 'signature') {
      const result = await mutate<MutationResult & { field?: { name: string } }>(doc, 'sign.addField', { page: page.id, rect })
      if (result) setTool(doc, 'design')
      return
    }
    const result = await mutate<FieldsResult>(doc, 'form.addFields', { page: page.id, fields: [{ type: spec.type, rect }] })
    id = result?.widgets?.[0]?.id
    if (id) {
      // Casillas y opciones suelen añadirse varias seguidas: la herramienta sigue activa.
      if (spec.type !== 'checkbox' && spec.type !== 'radio') setTool(doc, 'design')
      selectField(doc, page.id, id)
    }
  }

  return (
    <>
      {selected.length > 0 && !spec && <div className="over-layer catch" onPointerDown={() => selectField(doc, page.id, null)} />}
      <BoxLayer
        boxes={boxes}
        pxPerPt={pxPerPt}
        selected={selected}
        onSelect={onSelect}
        onChange={onChange}
        outline
        render={(box) => {
          const widget = widgets.find((w) => w.id === box.id)
          if (!widget || box.h * pxPerPt < 13 || box.w * pxPerPt < 34) return null
          return (
            <div className="field-box">
              {ICONS[widget.type]}
              <span>{widget.type === 'radio' ? `${widget.name} · ${widget.export ?? ''}` : widget.name}</span>
            </div>
          )
        }}
      />
      {suggestions && suggestions.length > 0 && !spec && (
        <div className="over-layer">
          {suggestions.map((s, index) => (
            <div
              key={index}
              className="suggestion"
              title={s.label ? fmt(L.form.detect.addFieldNamed, { label: s.label }) : L.form.detect.addField}
              style={{
                left: s.rect[0] * pxPerPt,
                top: s.rect[1] * pxPerPt,
                width: (s.rect[2] - s.rect[0]) * pxPerPt,
                height: (s.rect[3] - s.rect[1]) * pxPerPt
              }}
              onPointerDown={(event) => {
                event.stopPropagation()
                void acceptSuggestions(doc, page.id, [s])
              }}
            />
          ))}
        </div>
      )}
      {spec && (
        <DrawLayer
          pxPerPt={pxPerPt}
          gesture="rect"
          clickToo
          onRect={(rect) => void create(rect)}
          onPoint={(point) => void create([point[0], point[1] - spec.h / 2, point[0] + spec.w, point[1] + spec.h / 2])}
        />
      )}
    </>
  )
})

/** Un campo de firma vacío se firma con certificado al pulsarlo. */
function signField(doc: string, page: string, widget: Widget): void {
  useApp.getState().openDialog({ type: 'custom', id: 'certSign', doc, data: { field: widget.name, page } })
}

export function FormLayers(props: LayerProps) {
  if (designing(props.tool)) return <DesignLayer {...props} />
  return <FillLayer doc={props.doc} page={props.page} pxPerPt={props.pxPerPt} onSign={(w) => signField(props.doc, props.page.id, w)} />
}

/** En modo Ver los formularios también se pueden rellenar directamente. */
export function ViewLayers({ doc, page, pxPerPt, tool }: LayerProps) {
  if (tool === 'hand') return null
  return <FillLayer doc={doc} page={page} pxPerPt={pxPerPt} onSign={(w) => signField(doc, page.id, w)} />
}

export function FormOptions({ doc }: { doc: DocView }) {
  const list = useSuggestions((s) => s.items[`${doc.id}|${doc.current}`])
  const setList = useSuggestions((s) => s.set)
  if (doc.mode !== 'form' || doc.search.open) return null
  if (designing(doc.tool) && list && list.length > 0) {
    return (
      <SubBar>
        <SubLabel>{plural(L.form.detect.found, list.length)}</SubLabel>
        <button className="btn sm primary" onClick={() => void acceptSuggestions(doc.id, doc.current, list)}>
          {L.form.detect.addAll}
        </button>
        <button className="btn sm ghost" onClick={() => setList(`${doc.id}|${doc.current}`, [])}>
          {L.form.detect.dismiss}
        </button>
      </SubBar>
    )
  }
  if (doc.tool.startsWith('f-')) {
    return (
      <SubBar>
        <SubLabel>{L.form.hints.place}</SubLabel>
      </SubBar>
    )
  }
  if (!designing(doc.tool) && !doc.info.hasForm) {
    return (
      <SubBar>
        <SubLabel>{L.form.hints.noFields}</SubLabel>
      </SubBar>
    )
  }
  if (doc.info.hasXfa) {
    return (
      <SubBar>
        <SubLabel>{L.form.hints.xfa}</SubLabel>
      </SubBar>
    )
  }
  return null
}

// ----------------------------------------------------------- panel de propiedades

function OptionsEditor({ value, onCommit }: { value: string[]; onCommit: (options: string[]) => void }) {
  const [text, setText] = useState(value.join('\n'))
  useEffect(() => setText(value.join('\n')), [value])
  return (
    <textarea
      className="textarea"
      rows={Math.min(8, Math.max(3, value.length + 1))}
      value={text}
      spellCheck={false}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => {
        const options = text
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
        if (options.length > 0 && options.join('\n') !== value.join('\n')) onCommit(options)
      }}
      onKeyDown={(event) => event.stopPropagation()}
    />
  )
}

function TextProp({ value, onCommit, placeholder }: { value: string; onCommit: (value: string) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  return (
    <input
      className="input sm"
      value={draft}
      placeholder={placeholder}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => draft.trim() !== value && onCommit(draft.trim())}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
    />
  )
}

const TYPE_LABEL: Record<string, string> = {
  text: L.form.inspector.type.text,
  checkbox: L.form.inspector.type.checkbox,
  radio: L.form.inspector.type.radio,
  combo: L.form.inspector.type.combo,
  list: L.form.inspector.type.list,
  signature: L.form.inspector.type.signature
}

export function FieldInspector({ doc }: { doc: DocView }) {
  const selection = doc.selection?.kind === 'widget' && doc.selection.ids.length === 1 ? doc.selection : null
  const page = selection ? doc.pages.find((p) => p.id === selection.page) : undefined
  const data = usePageData<{ widgets: Widget[] }>(doc.id, page?.id ?? '', page?.gen ?? 0, 'widgets', Boolean(page))
  if (doc.mode !== 'form' || !designing(doc.tool) || !selection || !page) return null
  const widget = data?.widgets.find((w) => w.id === selection.ids[0])
  if (!widget) return null

  const update = (props: Record<string, unknown>): void => void mutate(doc.id, 'form.updateField', { page: page.id, id: widget.id, props })
  const color = (key: 'textColor' | 'fill' | 'border', value: Color | null): void => {
    if (value) update({ [key]: value })
    else update(key === 'fill' ? { clearFill: true } : { clearBorder: true })
  }
  const locked = widget.type === 'signature'
  return (
    <aside className="inspector">
      <div className="inspector-head">
        <span className="grow">{TYPE_LABEL[widget.type] ?? L.form.inspector.type.other}</span>
        <button className="icon-btn sm" data-tip={L.common.close} data-tip-side="left" onClick={() => selectField(doc.id, page.id, null)}>
          <X size={15} />
        </button>
      </div>
      <div className="inspector-body">
        <div className="group">
          {widget.type === 'radio' ? (
            <>
              <Field label={L.form.inspector.group} hint={L.form.inspector.groupHint}>
                <TextProp value={widget.group ?? widget.name} onCommit={(group) => group && update({ group })} />
              </Field>
              <Field label={L.form.inspector.optionValue}>
                <TextProp value={widget.export ?? ''} onCommit={(value) => value && update({ export: value })} />
              </Field>
            </>
          ) : (
            <Field label={L.form.inspector.name}>
              {locked ? <input className="input sm" value={widget.name} readOnly /> : <TextProp value={widget.name} onCommit={(name) => name && update({ name })} />}
            </Field>
          )}
          {!locked && (
            <Field label={L.form.inspector.description} hint={L.form.inspector.descriptionHint}>
              <TextProp value={widget.label} onCommit={(label) => update({ label })} placeholder={L.form.inspector.optional} />
            </Field>
          )}
        </div>

        {!locked && (
          <div className="group">
            <Checkbox checked={widget.required} onChange={(required) => update({ required })}>
              {L.form.inspector.required}
            </Checkbox>
            <Checkbox checked={widget.readOnly} onChange={(readOnly) => update({ readOnly })}>
              {L.form.inspector.readOnly}
            </Checkbox>
            {widget.type === 'text' && (
              <>
                <Checkbox checked={widget.multiline} onChange={(multiline) => update({ multiline })}>
                  {L.form.inspector.multiline}
                </Checkbox>
                <Checkbox checked={widget.password} onChange={(password) => update({ password })}>
                  {L.form.inspector.password}
                </Checkbox>
                <div className="row">
                  <span className="label grow">{L.form.inspector.maxLength}</span>
                  <NumberField value={widget.maxLength} min={0} max={10000} width={70} onChange={(maxLength) => update({ maxLength: Math.round(maxLength) })} />
                </div>
              </>
            )}
            {widget.type === 'checkbox' && (
              <Field label={L.form.inspector.checkedValue}>
                <TextProp value={widget.export ?? 'Yes'} onCommit={(value) => value && update({ export: value })} />
              </Field>
            )}
          </div>
        )}

        {(widget.type === 'combo' || widget.type === 'list') && (
          <div className="group">
            <span className="group-title">{L.form.inspector.options}</span>
            <OptionsEditor value={widget.options} onCommit={(options) => update({ options })} />
            <span className="hint">{L.form.inspector.optionsHint}</span>
          </div>
        )}

        {!locked && (
          <div className="group">
            <span className="group-title">{L.form.inspector.appearance}</span>
            {(widget.type === 'text' || widget.type === 'combo' || widget.type === 'list') && (
              <>
                <div className="row">
                  <span className="label grow">{L.form.inspector.font}</span>
                  <select className="select sm" style={{ width: 110 }} value={widget.font} onChange={(event) => update({ font: event.target.value })}>
                    <option value="sans">{L.form.inspector.fonts.sans}</option>
                    <option value="serif">{L.form.inspector.fonts.serif}</option>
                    <option value="mono">{L.form.inspector.fonts.mono}</option>
                  </select>
                </div>
                <div className="row">
                  <span className="label grow">{L.form.inspector.fontSize}</span>
                  <NumberField value={widget.fontSize} min={0} max={72} width={70} suffix="pt" onChange={(fontSize) => update({ fontSize })} />
                </div>
              </>
            )}
            {(widget.type === 'text' || widget.type === 'combo') && (
              <div className="row">
                <span className="label grow">{L.form.inspector.align}</span>
                <Segmented
                  value={String(widget.align)}
                  options={[
                    { value: '0', label: L.form.inspector.alignLeft },
                    { value: '1', label: L.form.inspector.alignCenter },
                    { value: '2', label: L.form.inspector.alignRight }
                  ]}
                  onChange={(align) => update({ align: Number(align) })}
                />
              </div>
            )}
            <div className="row">
              <span className="label grow">{L.form.inspector.textColor}</span>
              <ColorButton value={widget.textColor} onChange={(value) => color('textColor', value)} />
            </div>
            <div className="row">
              <span className="label grow">{L.form.inspector.fill}</span>
              <ColorButton value={widget.fill} allowNone onChange={(value) => color('fill', value)} />
            </div>
            <div className="row">
              <span className="label grow">{L.form.inspector.border}</span>
              <ColorButton value={widget.border} allowNone onChange={(value) => color('border', value)} />
              <NumberField
                value={widget.borderWidth}
                min={0}
                max={8}
                step={0.5}
                width={62}
                suffix="pt"
                onChange={(borderWidth) => update({ borderWidth })}
              />
            </div>
          </div>
        )}

        <div className="group">
          <div className="row">
            {!locked && (
              <button
                className="btn sm grow"
                onClick={() =>
                  void (async () => {
                    const result = await mutate<FieldsResult>(doc.id, 'form.duplicate', { page: page.id, ids: [widget.id] })
                    const copy = result?.widgets?.[0]
                    if (copy) selectField(doc.id, page.id, copy.id)
                  })()
                }
              >
                <Copy size={14} />
                {L.form.inspector.duplicate}
              </button>
            )}
            <button className="btn sm grow" disabled={widget.signed} onClick={() => void deleteSelection(doc.id)}>
              <Trash size={14} />
              {L.common.delete}
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}
