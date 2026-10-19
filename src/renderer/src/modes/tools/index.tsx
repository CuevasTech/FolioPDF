import {
  ArrowLeft,
  CircleAlert,
  Crop,
  Droplets,
  EyeOff,
  FileImage,
  FileText,
  FileType,
  Hash,
  Layers,
  Lock,
  LockOpen,
  Minimize2,
  PanelTop,
  ScanText,
  Search,
  ShieldCheck,
  Sparkles,
  SquareDashed,
  TextCursor,
  Trash,
  UserSearch
} from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import type { MutationResult, Rect, TextStyle } from '@shared/types'
import { type CustomDialogProps, registerDialog } from '@/components/Dialogs'
import { ActionButton, ToolButton } from '@/components/Toolbar'
import { Checkbox, Dialog, Field, Segmented } from '@/components/ui'
import { L, fmt, plural } from '@/i18n'
import { call, query, trackProgress, unwrap } from '@/lib/api'
import { runBusy } from '@/lib/busy'
import { formatBytes } from '@/lib/util'
import { mutate, setTool, toastError } from '@/state/actions'
import { type DocView, useApp } from '@/state/app'
import type { LayerProps } from '@/viewer/ModeLayers'
import { readSelection } from '../annotate/selection'
import { ColorButton, NumberField, SubBar, SubLabel } from '../common/controls'
import { DrawLayer } from '../common/DrawLayer'

const open = (id: string, doc: string): void => useApp.getState().openDialog({ type: 'custom', id, doc })
const all = (doc: DocView): string[] => doc.pages.map((p) => p.id)

// ------------------------------------------------------------------ portada

function Card({ icon, title, text, onClick, disabled }: { icon: ReactNode; title: string; text: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="tool-card" onClick={onClick} disabled={disabled}>
      <span className="ico">{icon}</span>
      <span>
        <b>{title}</b>
        <span>{text}</span>
      </span>
    </button>
  )
}

export function ToolsStage({ doc }: { doc: DocView }) {
  const exportAs = async (kind: 'text' | 'word', title: string): Promise<void> => {
    const tracker = trackProgress((info) =>
      useApp.getState().setBusy({ title, message: info.message, progress: info.total ? info.done / info.total : null })
    )
    try {
      useApp.getState().setBusy({ title, message: '', progress: null })
      const outcome = unwrap(await window.folio.files.exportAs(doc.id, kind, {}, tracker.callId))
      if (outcome.done) useApp.getState().toast('success', L.tools.exported)
    } catch (error) {
      toastError(error)
    } finally {
      tracker.done()
      useApp.getState().setBusy(null)
    }
  }
  const protectedNow = doc.state.protection === 'protected'
  return (
    <div className="tools-page">
      <div className="tools-inner">
        <div>
          <h2>{L.tools.home.title}</h2>
          <p className="lead">{L.tools.home.lead}</p>
        </div>

        <div className="tool-group">
          <h3>{L.tools.home.privacy}</h3>
          <div className="tool-cards">
            <Card icon={<ScanText size={18} />} title={L.tools.ocr.title} text={L.tools.ocr.card} onClick={() => open('ocr', doc.id)} />
            <Card icon={<EyeOff size={18} />} title={L.tools.redact.title} text={L.tools.redact.card} onClick={() => setTool(doc.id, 'redact')} />
            <Card icon={<Sparkles size={18} />} title={L.tools.sanitize.title} text={L.tools.sanitize.card} onClick={() => open('sanitize', doc.id)} />
          </div>
        </div>

        <div className="tool-group">
          <h3>{L.tools.home.protection}</h3>
          <div className="tool-cards">
            <Card icon={<Lock size={18} />} title={L.tools.protect.title} text={L.tools.protect.card} onClick={() => open('protect', doc.id)} />
            <Card
              icon={<LockOpen size={18} />}
              title={L.tools.unprotect.title}
              text={protectedNow ? L.tools.unprotect.card : L.tools.unprotect.notProtected}
              disabled={!protectedNow}
              onClick={() =>
                void (async () => {
                  const result = await mutate(doc.id, 'tools.unprotect')
                  if (result) useApp.getState().toast('success', L.tools.unprotect.done)
                })()
              }
            />
          </div>
        </div>

        <div className="tool-group">
          <h3>{L.tools.home.convert}</h3>
          <div className="tool-cards">
            <Card icon={<Minimize2 size={18} />} title={L.tools.optimize.title} text={L.tools.optimize.card} onClick={() => open('optimize', doc.id)} />
            <Card icon={<FileImage size={18} />} title={L.tools.images.title} text={L.tools.images.card} onClick={() => open('images', doc.id)} />
            <Card icon={<FileType size={18} />} title={L.tools.word.title} text={L.tools.word.card} onClick={() => void exportAs('word', L.tools.word.busy)} />
            <Card icon={<FileText size={18} />} title={L.tools.text.title} text={L.tools.text.card} onClick={() => void exportAs('text', L.tools.text.busy)} />
            <Card icon={<Crop size={18} />} title={L.tools.crop.title} text={L.tools.crop.card} onClick={() => setTool(doc.id, 'crop')} />
          </div>
        </div>

        <div className="tool-group">
          <h3>{L.tools.home.add}</h3>
          <div className="tool-cards">
            <Card icon={<Droplets size={18} />} title={L.tools.watermark.title} text={L.tools.watermark.card} onClick={() => open('watermark', doc.id)} />
            <Card icon={<PanelTop size={18} />} title={L.tools.headerFooter.cardTitle} text={L.tools.headerFooter.card} onClick={() => open('headerFooter', doc.id)} />
            <Card icon={<Hash size={18} />} title={L.tools.pageNumbers.title} text={L.tools.pageNumbers.card} onClick={() => open('pageNumbers', doc.id)} />
            <Card icon={<Trash size={18} />} title={L.tools.removeAdded.title} text={L.tools.removeAdded.card} onClick={() => open('removeAdded', doc.id)} />
            <Card
              icon={<Layers size={18} />}
              title={L.tools.flatten.title}
              text={L.tools.flatten.card}
              onClick={() =>
                useApp.getState().openDialog({
                  type: 'confirm',
                  title: L.tools.flatten.confirmTitle,
                  message: L.tools.flatten.confirmMessage,
                  confirmLabel: L.tools.flatten.title,
                  onConfirm: async () => {
                    await mutate(doc.id, 'form.flatten')
                    await mutate(doc.id, 'annot.flatten')
                  }
                })
              }
            />
          </div>
        </div>
      </div>
    </div>
  )
}

// -------------------------------------------------------------------- censura

interface Mark {
  page: string
  id: string
  rect: Rect
}

function useMarks(doc: DocView): Mark[] {
  const [marks, setMarks] = useState<Mark[]>([])
  const token = doc.state.token
  useEffect(() => {
    let alive = true
    query<{ marks: Mark[] }>('redact.list', { doc: doc.id })
      .then((result) => alive && setMarks(result.marks))
      .catch(() => alive && setMarks([]))
    return () => {
      alive = false
    }
  }, [doc.id, token])
  return marks
}

export function RedactTools({ doc }: { doc: DocView }) {
  const marks = useMarks(doc)
  const apply = (): void =>
    useApp.getState().openDialog({
      type: 'confirm',
      title: plural(L.tools.redact.confirmTitle, marks.length),
      message: doc.state.signed ? `${L.tools.redact.confirmMessage} ${L.tools.redact.signedNote}` : L.tools.redact.confirmMessage,
      confirmLabel: L.tools.redact.title,
      danger: true,
      option: {
        label: L.tools.redact.flattenOption,
        hint: L.tools.redact.flattenHint,
        value: false
      },
      onConfirm: async (flatten) => {
        const result = await runBusy(L.tools.redact.busy, (context) =>
          call<MutationResult>('redact.apply', { doc: doc.id, flatten }, { signal: context.signal, onProgress: context.onProgress, priority: 9 })
        )
        if (result) {
          useApp.getState().applyResult(doc.id, result.result)
          const info = result.result as MutationResult & {
            removedOcrPages?: string[]
            hiddenTextPages?: string[]
            sharedFields?: number
            alsoOnPages?: string[]
            flattenedPages?: string[]
          }
          const toast = useApp.getState().toast
          toast('success', L.tools.redact.done)
          if (info.flattenedPages?.length) {
            const count = info.flattenedPages.length
            toast('info', plural(L.tools.redact.flattened, count), { label: L.tools.redact.recognize, run: () => open('ocr', doc.id) })
          }
          // Una imagen o un dibujo compartido por varias páginas solo se ha
          // quitado de las censuradas: en las demás sigue a la vista (y en el archivo).
          if (info.alsoOnPages?.length) {
            const pages = useApp.getState().docs[doc.id]?.pages ?? []
            const numbers = info.alsoOnPages
              .map((id) => pages.findIndex((page) => page.id === id) + 1)
              .filter((n) => n > 0)
              .slice(0, 8)
            toast('warning', plural(L.tools.redact.alsoOn, numbers.length, { pages: numbers.join(', ') }))
          }
          // La capa de texto del OCR cubre la página entera y contiene lo
          // censurado: se quita completa. Volver a reconocer es seguro, porque
          // la imagen ya lleva el recuadro negro.
          if (info.removedOcrPages?.length && !info.flattenedPages?.length) {
            toast('info', L.tools.redact.ocrRemoved, { label: L.tools.redact.recognizeAgain, run: () => open('ocr', doc.id) })
          }
          // Texto que no se ve (de otro programa) y que puede seguir conteniendo datos.
          if (info.hiddenTextPages?.length && !info.flattenedPages?.length) {
            toast('warning', L.tools.redact.hiddenText, { label: L.tools.sanitize.run, run: () => open('sanitize', doc.id) })
          }
          if (info.sharedFields) {
            toast('warning', L.tools.redact.sharedFields)
          }
        }
      }
    })
  return (
    <>
      <ActionButton icon={<ArrowLeft size={16} />} label={L.tools.back} tip={L.tools.backTip} onClick={() => setTool(doc.id, '')} compact />
      <div className="sep" />
      <ToolButton doc={doc} tool="redact" icon={<SquareDashed size={16} />} label={L.tools.redact.markArea} tip={L.tools.redact.markAreaTip} />
      <ToolButton doc={doc} tool="redact-text" icon={<TextCursor size={16} />} label={L.tools.redact.markText} tip={L.tools.redact.markTextTip} />
      <ActionButton icon={<Search size={16} />} label={L.tools.redactFind.title} onClick={() => open('redactFind', doc.id)} />
      <ActionButton icon={<UserSearch size={16} />} label={L.ai.redact.open} tip={L.ai.redact.openTip} onClick={() => open('personalData', doc.id)} />
      <div className="sep" />
      <button className="btn sm danger" disabled={marks.length === 0} onClick={apply}>
        {marks.length > 0 ? fmt(L.tools.redact.applyCount, { n: marks.length }) : L.tools.redact.title}
      </button>
    </>
  )
}

function RedactLayerInner({ doc, page, pxPerPt, tool }: LayerProps & { view: DocView }) {
  return (
    <>
      {tool === 'redact' && (
        <DrawLayer
          pxPerPt={pxPerPt}
          gesture="rect"
          color="#c81e1e"
          fill="rgba(200,30,30,0.15)"
          onRect={(rect) => void mutate(doc, 'redact.mark', { page: page.id, rects: [rect] })}
        />
      )}
      {tool === 'crop' && <DrawLayer pxPerPt={pxPerPt} gesture="rect" color="#2563eb" fill="rgba(37,99,235,0.12)" onRect={(rect) => askCrop(doc, page.id, rect)} />}
    </>
  )
}

export function ToolsLayers(props: LayerProps) {
  const view = useApp((s) => s.docs[props.doc])
  if (!view || props.tool === '') return null
  return <RedactLayerInner {...props} view={view} />
}

// -------------------------------------------------------------------- recorte

function askCrop(docId: string, pageId: string, rect: Rect): void {
  const view = useApp.getState().docs[docId]
  if (!view) return
  const run = async (pages: string[], remove: boolean): Promise<void> => {
    const result = await mutate<MutationResult & { removedObjects?: number }>(docId, 'pages.crop', { pages, box: rect, remove })
    if (result) {
      useApp
        .getState()
        .toast('success', remove ? L.tools.crop.removed : L.tools.crop.hidden)
    }
  }
  useApp.getState().openDialog({
    type: 'confirm',
    title: L.tools.crop.title,
    message: L.tools.crop.message,
    confirmLabel: L.tools.crop.thisPage,
    alt: view.pages.length > 1 ? { label: L.tools.crop.allPages, run: (remove) => run(all(view), remove) } : undefined,
    option: {
      label: L.tools.crop.removeOption,
      hint: L.tools.crop.removeHint,
      value: true
    },
    onConfirm: (remove) => run([pageId], remove)
  })
}

function CropTools({ doc }: { doc: DocView }) {
  return (
    <>
      <ActionButton icon={<ArrowLeft size={16} />} label={L.tools.back} tip={L.tools.backTip} onClick={() => setTool(doc.id, '')} compact />
      <div className="sep" />
      <span className="toolbar-hint">
        <Crop size={15} />
        {L.tools.crop.hint}
      </span>
    </>
  )
}

/** Barra de la herramienta activa de «Más»: censura o recorte. */
export function ToolsToolbar({ doc }: { doc: DocView }) {
  return doc.tool === 'crop' ? <CropTools doc={doc} /> : <RedactTools doc={doc} />
}

/** Marcas de censura pendientes y marcado por selección de texto. */
export function RedactOverlay({ doc }: { doc: DocView }) {
  const active = doc.mode === 'tools' && doc.tool.startsWith('redact')
  const marks = useMarks(doc)
  const [, refresh] = useState(0)

  // Las marcas se pintan dentro de cada página visible; hay que repintar al desplazarse.
  useEffect(() => {
    if (!active) return
    const viewer = document.querySelector<HTMLElement>(`.viewer[data-doc="${doc.id}"]`)
    if (!viewer) return
    const onScroll = (): void => refresh((n) => n + 1)
    viewer.addEventListener('scroll', onScroll, { passive: true })
    const onUp = (): void => {
      if (useApp.getState().docs[doc.id]?.tool !== 'redact-text') return
      window.setTimeout(() => {
        const selected = readSelection(doc.id)
        if (!selected) return
        window.getSelection()?.removeAllRanges()
        for (const [page, rects] of selected.pages) void mutate(doc.id, 'redact.mark', { page, rects })
      }, 0)
    }
    viewer.addEventListener('pointerup', onUp)
    return () => {
      viewer.removeEventListener('scroll', onScroll)
      viewer.removeEventListener('pointerup', onUp)
    }
  }, [active, doc.id])

  if (!active) return null
  const boxes: ReactNode[] = []
  for (const mark of marks) {
    const host = document.querySelector<HTMLElement>(`.viewer[data-doc="${doc.id}"] .page[data-page="${mark.page}"]`)
    const info = doc.pages.find((p) => p.id === mark.page)
    const stage = document.querySelector<HTMLElement>('.stage')
    if (!host || !info || !stage) continue
    const bounds = host.getBoundingClientRect()
    const origin = stage.getBoundingClientRect()
    const scale = bounds.width / info.w
    boxes.push(
      <div
        key={`${mark.page}-${mark.id}`}
        className="redact-mark"
        title={L.tools.redact.removeMarkTip}
        style={{
          left: bounds.left - origin.left + mark.rect[0] * scale,
          top: bounds.top - origin.top + mark.rect[1] * scale,
          width: (mark.rect[2] - mark.rect[0]) * scale,
          height: (mark.rect[3] - mark.rect[1]) * scale,
          zIndex: 6
        }}
        onClick={() => void mutate(doc.id, 'redact.unmark', { page: mark.page, ids: [mark.id] })}
      />
    )
  }
  return (
    <>
      {boxes}
      <SubBar>
        <CircleAlert size={15} color="var(--danger)" />
        <SubLabel>
          {marks.length === 0 ? L.tools.redact.hintEmpty : plural(L.tools.redact.hintMarked, marks.length)}
        </SubLabel>
      </SubBar>
    </>
  )
}

// ------------------------------------------------------------------- diálogos

function useDoc(id: string | undefined): DocView | undefined {
  return useApp((s) => (id ? s.docs[id] : undefined))
}

function Footer({ onRun, label, disabled }: { onRun: () => void; label: string; disabled?: boolean }) {
  const close = useApp((s) => s.closeDialog)
  return (
    <>
      <button className="btn" onClick={close}>
        {L.common.cancel}
      </button>
      <button className="btn primary" disabled={disabled} onClick={onRun}>
        {label}
      </button>
    </>
  )
}

const PERMISSIONS = ['print', 'copy', 'modify', 'annotate', 'form', 'assemble'] as const

function ProtectDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [user, setUser] = useState('')
  const [owner, setOwner] = useState('')
  const [repeat, setRepeat] = useState('')
  const [perms, setPerms] = useState<Record<string, boolean>>({ print: true, copy: true, modify: false, annotate: true, form: true, assemble: false })
  if (!doc) return null
  const mismatch = repeat.length > 0 && repeat !== owner
  const same = user.length > 0 && user === owner
  // El cifrado es tan fuerte como la contraseña de apertura: se avisa si es corta.
  const weak = user.length > 0 && user.length < 8
  const valid = owner.length >= 4 && owner === repeat && !same && owner.length <= 40 && user.length <= 40
  const run = async (): Promise<void> => {
    const result = await mutate(doc.id, 'tools.protect', {
      userPassword: user,
      ownerPassword: owner,
      permissions: { ...perms, accessibility: true, printHq: perms.print }
    })
    if (result) {
      close()
      useApp.getState().toast('success', L.tools.protect.done)
    }
  }
  return (
    <Dialog title={L.tools.protect.title} size="wide" icon={<ShieldCheck size={18} color="var(--accent-fg)" />} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.protect.run} disabled={!valid} />}>
      <Field label={L.tools.protect.userLabel} hint={L.tools.protect.userHint}>
        <input className="input" type="password" autoComplete="new-password" maxLength={40} value={user} onChange={(e) => setUser(e.target.value)} />
      </Field>
      <div className="form-grid">
        <Field label={L.tools.protect.ownerLabel} hint={L.tools.protect.ownerHint}>
          <input className="input" type="password" autoComplete="new-password" maxLength={40} value={owner} onChange={(e) => setOwner(e.target.value)} />
        </Field>
        <Field label={L.tools.protect.repeatLabel}>
          <input className="input" type="password" autoComplete="new-password" maxLength={40} value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
      </div>
      {mismatch && <span className="error-text">{L.tools.protect.mismatch}</span>}
      {same && <span className="error-text">{L.tools.protect.same}</span>}
      {weak && (
        <div className="notice warn">
          <CircleAlert size={15} />
          <span>{L.tools.protect.weak}</span>
        </div>
      )}
      {doc.state.signed && (
        <div className="notice warn">
          <CircleAlert size={15} />
          <span>{L.tools.protect.signed}</span>
        </div>
      )}
      <div className="field">
        <label>{L.tools.protect.permsLabel}</label>
        <div className="perm-grid">
          {PERMISSIONS.map((key) => (
            <Checkbox key={key} checked={perms[key]} onChange={(value) => setPerms({ ...perms, [key]: value })}>
              {L.tools.protect.perms[key]}
            </Checkbox>
          ))}
        </div>
      </div>
      <div className="notice">
        <Lock size={15} />
        <span>{L.tools.protect.note}</span>
      </div>
    </Dialog>
  )
}

function SanitizeDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [opts, setOpts] = useState({ metadata: true, attachments: true, javascript: true, hiddenText: true, thumbnails: true, comments: false, links: false })
  if (!doc) return null
  const rows: [keyof typeof opts, string][] = [
    ['metadata', L.tools.sanitize.items.metadata],
    ['attachments', L.tools.sanitize.items.attachments],
    ['javascript', L.tools.sanitize.items.javascript],
    ['hiddenText', L.tools.sanitize.items.hiddenText],
    ['thumbnails', L.tools.sanitize.items.thumbnails],
    ['comments', L.tools.sanitize.items.comments],
    ['links', L.tools.sanitize.items.links]
  ]
  const run = async (): Promise<void> => {
    close()
    const result = await mutate(doc.id, 'tools.sanitize', opts)
    if (result) useApp.getState().toast('success', L.tools.sanitize.done)
  }
  return (
    <Dialog title={L.tools.sanitize.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.sanitize.run} disabled={!Object.values(opts).some(Boolean)} />}>
      <p>{L.tools.sanitize.intro}</p>
      {rows.map(([key, label]) => (
        <Checkbox key={key} checked={opts[key]} onChange={(value) => setOpts({ ...opts, [key]: value })}>
          {label}
        </Checkbox>
      ))}
      {doc.state.signed && (
        <div className="notice warn">
          <CircleAlert size={15} />
          <span>{L.tools.sanitize.signed}</span>
        </div>
      )}
    </Dialog>
  )
}

function OptimizeDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [preset, setPreset] = useState<'light' | 'balanced' | 'strong'>('balanced')
  const [grayscale, setGrayscale] = useState(false)
  if (!doc) return null
  const run = async (): Promise<void> => {
    close()
    useApp.getState().setBusy({ title: L.tools.optimize.busy, message: L.tools.optimize.busyMessage, progress: null })
    try {
      const outcome = unwrap(await window.folio.files.exportAs(doc.id, 'optimize', { preset, grayscale }))
      if (!outcome.done) return
      const before = Number(outcome.detail?.before ?? 0)
      const after = Number(outcome.detail?.after ?? 0)
      if (before > 0 && after > 0 && after < before) {
        useApp
          .getState()
          .toast('success', fmt(L.tools.optimize.saved, { before: formatBytes(before), after: formatBytes(after), percent: Math.round((1 - after / before) * 100) }))
      } else {
        useApp.getState().toast('info', L.tools.optimize.savedLittle)
      }
    } catch (error) {
      toastError(error)
    } finally {
      useApp.getState().setBusy(null)
    }
  }
  return (
    <Dialog title={L.tools.optimize.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.optimize.run} />}>
      <p>{fmt(L.tools.optimize.current, { size: formatBytes(doc.info.fileSize) })}</p>
      <Segmented
        full
        value={preset}
        options={[
          { value: 'light', label: L.tools.optimize.light },
          { value: 'balanced', label: L.tools.optimize.balanced },
          { value: 'strong', label: L.tools.optimize.strong }
        ]}
        onChange={setPreset}
      />
      <span className="hint">
        {preset === 'light' && L.tools.optimize.lightHint}
        {preset === 'balanced' && L.tools.optimize.balancedHint}
        {preset === 'strong' && L.tools.optimize.strongHint}
      </span>
      <Checkbox checked={grayscale} onChange={setGrayscale}>
        {L.tools.optimize.grayscale}
      </Checkbox>
      {doc.state.signed && (
        <div className="notice warn">
          <CircleAlert size={15} />
          <span>{L.tools.optimize.signed}</span>
        </div>
      )}
    </Dialog>
  )
}

function ImagesDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [format, setFormat] = useState<'png' | 'jpeg'>('png')
  const [dpi, setDpi] = useState(150)
  if (!doc) return null
  const run = async (): Promise<void> => {
    close()
    const title = L.tools.images.busy
    const tracker = trackProgress((info) =>
      useApp.getState().setBusy({ title, message: info.message, progress: info.total ? info.done / info.total : null })
    )
    try {
      useApp.getState().setBusy({ title, message: '', progress: null })
      const outcome = unwrap(await window.folio.files.exportAs(doc.id, 'images', { format, dpi }, tracker.callId))
      if (outcome.done) {
        // Las páginas muy grandes se exportan a menos resolución para no agotar la memoria.
        const reduced = Array.isArray(outcome.detail?.reduced) ? outcome.detail.reduced.length : 0
        const saved = outcome.files?.length ?? 0
        useApp
          .getState()
          .toast('success', reduced > 0 ? plural(L.tools.images.savedReduced, saved, { reduced }) : plural(L.tools.images.saved, saved))
      }
    } catch (error) {
      toastError(error)
    } finally {
      tracker.done()
      useApp.getState().setBusy(null)
    }
  }
  return (
    <Dialog title={L.tools.images.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.images.run} />}>
      <div className="row">
        <span className="grow">{L.tools.images.format}</span>
        <Segmented
          value={format}
          options={[
            { value: 'png', label: 'PNG' },
            { value: 'jpeg', label: 'JPEG' }
          ]}
          onChange={setFormat}
        />
      </div>
      <div className="row">
        <span className="grow">{L.tools.images.resolution}</span>
        <Segmented
          value={String(dpi)}
          options={[
            { value: '96', label: L.tools.images.screen },
            { value: '150', label: L.tools.images.normal },
            { value: '300', label: L.tools.images.print }
          ]}
          onChange={(value) => setDpi(Number(value))}
        />
      </div>
      <p>{plural(L.tools.images.count, doc.pages.length)}</p>
    </Dialog>
  )
}

const DEFAULT_STYLE: TextStyle = { font: 'Arial', bold: true, italic: false, size: 48, color: [0.6, 0.62, 0.68], align: 'center', lineHeight: 1.2 }

function WatermarkDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const fonts = useApp((s) => s.fonts)
  const [kind, setKind] = useState<'text' | 'image'>('text')
  const [text, setText] = useState(L.tools.watermark.defaultText)
  const [style, setStyle] = useState(DEFAULT_STYLE)
  const [image, setImage] = useState<{ name: string; data: Uint8Array } | null>(null)
  const [opacity, setOpacity] = useState(25)
  const [diagonal, setDiagonal] = useState(true)
  const [scale, setScale] = useState(60)
  const [background, setBackground] = useState(false)
  const [tile, setTile] = useState(false)
  if (!doc) return null
  const ready = kind === 'text' ? text.trim().length > 0 : image !== null
  const run = async (): Promise<void> => {
    close()
    const params: Record<string, unknown> = {
      doc: doc.id,
      pages: all(doc),
      opacity: opacity / 100,
      angle: diagonal ? -45 : 0,
      scale: scale / 100,
      background,
      tile
    }
    if (kind === 'text') Object.assign(params, { text: text.trim(), style })
    const result = await runBusy(L.tools.watermark.busy, (context) =>
      call<MutationResult>('tools.watermark', params, {
        binary: kind === 'image' ? image?.data : undefined,
        signal: context.signal,
        onProgress: context.onProgress,
        priority: 9
      })
    )
    if (result) useApp.getState().applyResult(doc.id, result.result)
  }
  return (
    <Dialog title={L.tools.watermark.title} size="wide" onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.watermark.run} disabled={!ready} />}>
      <Segmented
        value={kind}
        options={[
          { value: 'text', label: L.tools.watermark.text },
          { value: 'image', label: L.tools.watermark.image }
        ]}
        onChange={setKind}
      />
      {kind === 'text' ? (
        <>
          <Field label={L.tools.watermark.text}>
            <input className="input" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
          </Field>
          <div className="row">
            <select className="select sm" style={{ width: 150 }} value={style.font} onChange={(e) => setStyle({ ...style, font: e.target.value })}>
              {fonts.map((font) => (
                <option key={font.family}>{font.family}</option>
              ))}
            </select>
            <Checkbox checked={style.bold} onChange={(bold) => setStyle({ ...style, bold })}>
              {L.tools.watermark.bold}
            </Checkbox>
            <div className="grow" />
            <span className="label">{L.tools.watermark.color}</span>
            <ColorButton value={style.color} onChange={(color) => color && setStyle({ ...style, color })} />
          </div>
        </>
      ) : (
        <div className="row">
          <button
            className="btn"
            onClick={() =>
              void (async () => {
                try {
                  const picked = unwrap(await window.folio.files.pickImage())
                  if (picked) setImage(picked)
                } catch (error) {
                  toastError(error)
                }
              })()
            }
          >
            <FileImage size={15} />
            {L.tools.watermark.pickImage}
          </button>
          <span className="hint grow">{image ? image.name : L.tools.watermark.imageHint}</span>
        </div>
      )}
      <div className="form-grid">
        <div className="row">
          <span className="grow">{L.tools.watermark.opacity}</span>
          <NumberField value={opacity} min={5} max={100} step={5} suffix="%" width={72} onChange={setOpacity} />
        </div>
        <div className="row">
          <span className="grow">{L.tools.watermark.size}</span>
          <NumberField value={scale} min={5} max={100} step={5} suffix="%" width={72} onChange={setScale} />
        </div>
      </div>
      <Checkbox checked={diagonal} onChange={setDiagonal}>
        {L.tools.watermark.diagonal}
      </Checkbox>
      <Checkbox checked={tile} onChange={setTile}>
        {L.tools.watermark.tile}
      </Checkbox>
      <Checkbox checked={background} onChange={setBackground}>
        {L.tools.watermark.background}
      </Checkbox>
    </Dialog>
  )
}

function HeaderFooterDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [header, setHeader] = useState({ left: '', center: '', right: '' })
  const [footer, setFooter] = useState({ left: '', center: '', right: '' })
  const [size, setSize] = useState(9)
  if (!doc) return null
  const any = [...Object.values(header), ...Object.values(footer)].some((v) => v.trim())
  const run = async (): Promise<void> => {
    close()
    const result = await runBusy(L.tools.headerFooter.busy, (context) =>
      call<MutationResult>(
        'tools.headerFooter',
        { doc: doc.id, pages: all(doc), header, footer, style: { ...DEFAULT_STYLE, bold: false, size, color: [0.25, 0.27, 0.32], align: 'left' } },
        { signal: context.signal, onProgress: context.onProgress, priority: 9 }
      )
    )
    if (result) useApp.getState().applyResult(doc.id, result.result)
  }
  const line = (value: typeof header, set: (v: typeof header) => void): ReactNode => (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
      {(['left', 'center', 'right'] as const).map((key) => (
        <input
          key={key}
          className="input"
          maxLength={300}
          placeholder={L.tools.headerFooter[key]}
          value={value[key]}
          onChange={(e) => set({ ...value, [key]: e.target.value })}
        />
      ))}
    </div>
  )
  return (
    <Dialog title={L.tools.headerFooter.title} size="wide" onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.headerFooter.run} disabled={!any} />}>
      <Field label={L.tools.headerFooter.header}>{line(header, setHeader)}</Field>
      <Field label={L.tools.headerFooter.footer} hint={L.tools.headerFooter.hint}>
        {line(footer, setFooter)}
      </Field>
      <div className="row">
        <span className="grow">{L.tools.headerFooter.fontSize}</span>
        <NumberField value={size} min={5} max={30} suffix="pt" width={72} onChange={setSize} />
      </div>
    </Dialog>
  )
}

function PageNumbersDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [format, setFormat] = useState('{page}')
  const [position, setPosition] = useState('bottom-center')
  const [start, setStart] = useState(1)
  const [size, setSize] = useState(10)
  if (!doc) return null
  const run = async (): Promise<void> => {
    close()
    const result = await runBusy(L.tools.pageNumbers.busy, (context) =>
      call<MutationResult>(
        'tools.pageNumbers',
        { doc: doc.id, pages: all(doc), format, position, start, style: { ...DEFAULT_STYLE, bold: false, size, color: [0.25, 0.27, 0.32], align: 'left' } },
        { signal: context.signal, onProgress: context.onProgress, priority: 9 }
      )
    )
    if (result) useApp.getState().applyResult(doc.id, result.result)
  }
  return (
    <Dialog title={L.tools.pageNumbers.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.pageNumbers.run} disabled={!format.trim()} />}>
      <Field label={L.tools.pageNumbers.format}>
        <select className="select" value={format} onChange={(e) => setFormat(e.target.value)}>
          <option value="{page}">1, 2, 3…</option>
          <option value={L.tools.pageNumbers.formatPage}>{fmt(L.tools.pageNumbers.formatPage, { page: 1 })}</option>
          <option value="{page} / {pages}">1 / {doc.pages.length}</option>
          <option value={L.tools.pageNumbers.formatPageOf}>{fmt(L.tools.pageNumbers.formatPageOf, { page: 1, pages: doc.pages.length })}</option>
          <option value="- {page} -">- 1 -</option>
        </select>
      </Field>
      <Field label={L.tools.pageNumbers.position}>
        <select className="select" value={position} onChange={(e) => setPosition(e.target.value)}>
          <option value="bottom-center">{L.tools.pageNumbers.positions.bottomCenter}</option>
          <option value="bottom-right">{L.tools.pageNumbers.positions.bottomRight}</option>
          <option value="bottom-left">{L.tools.pageNumbers.positions.bottomLeft}</option>
          <option value="top-center">{L.tools.pageNumbers.positions.topCenter}</option>
          <option value="top-right">{L.tools.pageNumbers.positions.topRight}</option>
          <option value="top-left">{L.tools.pageNumbers.positions.topLeft}</option>
        </select>
      </Field>
      <div className="form-grid">
        <div className="row">
          <span className="grow">{L.tools.pageNumbers.start}</span>
          <NumberField value={start} min={0} max={99999} width={72} onChange={(v) => setStart(Math.round(v))} />
        </div>
        <div className="row">
          <span className="grow">{L.tools.pageNumbers.size}</span>
          <NumberField value={size} min={5} max={30} suffix="pt" width={72} onChange={setSize} />
        </div>
      </div>
    </Dialog>
  )
}

const ADDED = ['watermark', 'header', 'footer', 'pagenum', 'ocr'] as const

function RemoveAddedDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [counts, setCounts] = useState<Record<string, number> | null>(null)
  const [chosen, setChosen] = useState<string[]>([])
  useEffect(() => {
    if (!docId) return
    query<{ counts: Record<string, number> }>('tools.listAdded', { doc: docId })
      .then((result) => {
        setCounts(result.counts)
        setChosen(Object.keys(result.counts).filter((key) => result.counts[key] > 0))
      })
      .catch(() => setCounts({}))
  }, [docId])
  if (!doc) return null
  const available = ADDED.filter((key) => (counts?.[key] ?? 0) > 0)
  const run = async (): Promise<void> => {
    close()
    await mutate(doc.id, 'tools.removeAdded', { kinds: chosen })
  }
  return (
    <Dialog title={L.tools.removeAdded.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.removeAdded.run} disabled={chosen.length === 0} />}>
      {counts === null ? (
        <div className="row">
          <div className="spinner" />
          <span>{L.tools.removeAdded.searching}</span>
        </div>
      ) : available.length === 0 ? (
        <p>{L.tools.removeAdded.nothing}</p>
      ) : (
        available.map((key) => (
          <Checkbox key={key} checked={chosen.includes(key)} onChange={(value) => setChosen(value ? [...chosen, key] : chosen.filter((k) => k !== key))}>
            {L.tools.removeAdded.kinds[key]} ({counts[key]})
          </Checkbox>
        ))
      )}
    </Dialog>
  )
}

function OcrDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [status, setStatus] = useState<{ available: boolean; languages: string[] } | null>(null)
  const [languages, setLanguages] = useState<string[]>(['spa', 'eng'])
  const [skipText, setSkipText] = useState(true)
  const [straighten, setStraighten] = useState(true)
  useEffect(() => {
    query<{ available: boolean; languages: string[] }>('tools.ocrStatus')
      .then((result) => {
        setStatus(result)
        setLanguages((current) => current.filter((l) => result.languages.includes(l)))
      })
      .catch(() => setStatus({ available: false, languages: [] }))
  }, [])
  if (!doc) return null
  const names: Record<string, string> = L.tools.ocr.languageNames
  const run = async (): Promise<void> => {
    close()
    const result = await runBusy(
      L.tools.ocr.busy,
      (context) =>
        call<MutationResult & { recognized?: number; skipped?: number; empty?: number; straightened?: string[] }>(
          'tools.ocr',
          { doc: doc.id, pages: all(doc), languages, skipText, straighten },
          { signal: context.signal, onProgress: context.onProgress, priority: 9 }
        ),
      L.tools.ocr.analyzing
    )
    if (!result) return
    useApp.getState().applyResult(doc.id, result.result)
    const done = Number(result.result.recognized ?? 0)
    const empty = Number(result.result.empty ?? 0)
    const skipped = Number(result.result.skipped ?? 0)
    const turned = result.result.straightened?.length ?? 0
    const pages = (n: number): string => plural(L.common.pageCount, n)
    if (done > 0) {
      useApp
        .getState()
        .toast('success', empty > 0 ? fmt(L.tools.ocr.recognizedSomeEmpty, { pages: pages(done), empty: pages(empty) }) : fmt(L.tools.ocr.recognized, { pages: pages(done) }))
      if (turned > 0) useApp.getState().toast('info', plural(L.tools.ocr.straightened, turned))
    } else if (empty > 0) {
      useApp.getState().toast('info', fmt(L.tools.ocr.nothingFound, { pages: pages(empty) }))
    } else if (skipped > 0) {
      useApp.getState().toast('info', L.tools.ocr.allHadText)
    }
  }
  return (
    <Dialog title={L.tools.ocr.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.ocr.run} disabled={!status?.available || languages.length === 0} />}>
      {status === null ? (
        <div className="row">
          <div className="spinner" />
        </div>
      ) : !status.available ? (
        <div className="notice warn">
          <CircleAlert size={15} />
          <span>{L.tools.ocr.missingData}</span>
        </div>
      ) : (
        <>
          <p>{L.tools.ocr.intro}</p>
          <div className="field">
            <label>{L.tools.ocr.languages}</label>
            {status.languages
              .filter((l) => l !== 'osd')
              .map((code) => (
                <Checkbox key={code} checked={languages.includes(code)} onChange={(value) => setLanguages(value ? [...languages, code] : languages.filter((l) => l !== code))}>
                  {names[code] ?? code}
                </Checkbox>
              ))}
          </div>
          <Checkbox checked={skipText} onChange={setSkipText}>
            {L.tools.ocr.skipText}
          </Checkbox>
          <Checkbox checked={straighten} onChange={setStraighten}>
            {L.tools.ocr.straighten}
          </Checkbox>
        </>
      )}
    </Dialog>
  )
}

function RedactFindDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useDoc(docId)
  const [mode, setModeValue] = useState<'query' | 'pattern'>('query')
  const [text, setText] = useState('')
  const [pattern, setPattern] = useState('dni')
  if (!doc) return null
  const run = async (): Promise<void> => {
    close()
    const result = await mutate<MutationResult & { total?: number }>(doc.id, 'redact.find', mode === 'query' ? { query: text.trim() } : { pattern })
    if (result) {
      const count = Number(result.total ?? 0)
      useApp.getState().toast(count ? 'success' : 'info', count ? plural(L.tools.redactFind.found, count) : L.tools.redactFind.none)
    }
  }
  return (
    <Dialog title={L.tools.redactFind.title} onClose={close} footer={<Footer onRun={() => void run()} label={L.tools.redactFind.run} disabled={mode === 'query' && !text.trim()} />}>
      <Segmented
        full
        value={mode}
        options={[
          { value: 'query', label: L.tools.redactFind.byText },
          { value: 'pattern', label: L.tools.redactFind.byPattern }
        ]}
        onChange={setModeValue}
      />
      {mode === 'query' ? (
        <Field label={L.tools.redactFind.textLabel}>
          <input className="input" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
        </Field>
      ) : (
        <Field label={L.tools.redactFind.patternLabel}>
          <select className="select" value={pattern} onChange={(e) => setPattern(e.target.value)}>
            <option value="dni">{L.tools.redactFind.patterns.dni}</option>
            <option value="nie">{L.tools.redactFind.patterns.nie}</option>
            <option value="email">{L.tools.redactFind.patterns.email}</option>
            <option value="phone">{L.tools.redactFind.patterns.phone}</option>
            <option value="iban">{L.tools.redactFind.patterns.iban}</option>
            <option value="card">{L.tools.redactFind.patterns.card}</option>
          </select>
        </Field>
      )}
      <p>{L.tools.redactFind.note}</p>
    </Dialog>
  )
}

registerDialog('protect', ProtectDialog)
registerDialog('sanitize', SanitizeDialog)
registerDialog('optimize', OptimizeDialog)
registerDialog('images', ImagesDialog)
registerDialog('watermark', WatermarkDialog)
registerDialog('headerFooter', HeaderFooterDialog)
registerDialog('pageNumbers', PageNumbersDialog)
registerDialog('removeAdded', RemoveAddedDialog)
registerDialog('ocr', OcrDialog)
registerDialog('redactFind', RedactFindDialog)
