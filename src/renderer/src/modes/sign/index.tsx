import {
  BadgeCheck,
  CalendarDays,
  Check,
  CircleAlert,
  CircleCheck,
  CircleX,
  FileKey,
  MousePointer2,
  PenLine,
  Plus,
  ShieldCheck,
  Signature,
  TriangleAlert,
  Type,
  X
} from 'lucide-react'
import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'
import type { DateStyle, MutationResult, Point, Rect, SignLevel, TextStyle } from '@shared/types'
import { type CustomDialogProps, registerDialog } from '@/components/Dialogs'
import { ActionButton, ToolButton } from '@/components/Toolbar'
import { Checkbox, Dialog, Field, MenuButton, type MenuEntry, Segmented } from '@/components/ui'
import { fmt, L, locale } from '@/i18n'
import { errorMessage, FolioError, query, unwrap } from '@/lib/api'
import { DATE_STYLES, formatDateStyle } from '@/lib/dates'
import { timestampNameOf, timestampUrlOf } from '@/lib/timestamp'
import { cn, formatDate } from '@/lib/util'
import { saveDocAs, setTool, toastError, updateSettings } from '@/state/actions'
import { type DocView, useApp } from '@/state/app'
import type { LayerProps } from '@/viewer/ModeLayers'
import { ColorButton, NumberField, SubBar, SubLabel } from '../common/controls'
import { DrawLayer } from '../common/DrawLayer'
import { TextEditor, textPadding } from '../common/TextEditor'
import { useTools } from '../common/toolState'
import { ObjectOptions } from '../edit'
import { addImage, addShape, addText, selectObject } from '../edit/objects'
import { ObjectsLayer } from '../edit/ObjectsLayer'
import { exportTrimmed, imageToInk, INKS, SCRIPT_FONTS, type SignatureImage, useSignatures } from './signatures'

const fillStyle = (size: number, color: [number, number, number]): TextStyle => ({
  font: 'Arial',
  bold: false,
  italic: false,
  size,
  color,
  align: 'left',
  lineHeight: 1.2
})

/** Tipos de firma con certificado, de menos a más (ver SignLevel). */
const SIGN_LEVELS: { value: SignLevel; title: string; text: (service: string) => string }[] = [
  {
    value: 'basic',
    title: L.sign.cert.levels.basic.title,
    text: () => L.sign.cert.levels.basic.text
  },
  {
    value: 'timestamp',
    title: L.sign.cert.levels.timestamp.title,
    text: (service) => fmt(L.sign.cert.levels.timestamp.text, { service: service || L.sign.cert.levels.timestamp.anyService })
  },
  {
    value: 'longTerm',
    title: L.sign.cert.levels.longTerm.title,
    text: () => L.sign.cert.levels.longTerm.text
  }
]

function openCert(doc: string, data: Record<string, unknown>): void {
  useApp.getState().openDialog({ type: 'custom', id: 'certSign', doc, data })
}

// ------------------------------------------------------------------- barra

export function SignTools({ doc }: { doc: DocView }) {
  const { list, urls, active, load, setActive } = useSignatures()
  useEffect(() => void load(), [load])

  const signatureMenu = (): MenuEntry[] => [
    ...list.map(
      (item, index): MenuEntry => ({
        label: fmt(item.kind === 'initials' ? L.sign.tools.initialsItem : L.sign.tools.signatureItem, { n: index + 1 }),
        icon: <img src={urls[item.id]} alt="" style={{ width: 54, height: 22, objectFit: 'contain', background: '#fff', borderRadius: 4 }} />,
        checked: doc.tool === 'sig-place' && active === item.id,
        onSelect: () => {
          setActive(item.id)
          setTool(doc.id, 'sig-place')
        }
      })
    ),
    ...(list.length ? (['sep'] as MenuEntry[]) : []),
    { label: L.sign.tools.newSignature, icon: <Plus size={16} />, onSelect: () => useApp.getState().openDialog({ type: 'custom', id: 'signaturePad', doc: doc.id }) },
    ...(list.length
      ? ([{ label: L.sign.tools.manage, icon: <PenLine size={16} />, onSelect: () => useApp.getState().openDialog({ type: 'custom', id: 'signatureList', doc: doc.id }) }] as MenuEntry[])
      : [])
  ]

  return (
    <>
      <ToolButton doc={doc} tool="" icon={<MousePointer2 size={16} />} label={L.sign.tools.select} compact />
      <div className="sep" />
      <MenuButton className={doc.tool === 'sig-place' ? 'tool on' : 'tool'} entries={signatureMenu} tip={L.sign.tools.signatureTip}>
        <Signature size={16} />
        <span>{L.sign.tools.signature}</span>
      </MenuButton>
      <ToolButton doc={doc} tool="fill-text" icon={<Type size={16} />} label={L.sign.tools.text} tip={L.sign.tools.textTip} />
      <ToolButton doc={doc} tool="check" icon={<Check size={16} />} label={L.sign.tools.check} compact />
      <ToolButton doc={doc} tool="cross" icon={<X size={16} />} label={L.sign.tools.cross} compact />
      <ToolButton doc={doc} tool="date" icon={<CalendarDays size={16} />} label={L.sign.tools.date} compact />
      <div className="sep" />
      <ToolButton doc={doc} tool="cert-rect" icon={<FileKey size={16} />} label={L.sign.tools.cert} tip={L.sign.tools.certTip} />
      <ActionButton
        icon={<BadgeCheck size={16} />}
        label={L.sign.tools.signatures}
        tip={L.sign.tools.signaturesTip}
        onClick={() => useApp.getState().openDialog({ type: 'custom', id: 'signatureCheck', doc: doc.id })}
        compact
      />
    </>
  )
}

// -------------------------------------------------------------------- capas

export function SignLayers({ doc, page, pxPerPt, tool }: LayerProps) {
  const [adding, setAdding] = useState<Point | null>(null)
  const fill = useTools((s) => s.fill)
  const dateStyle = useApp((s) => s.settings.dateStyle)
  const hasSelection = useApp((s) => s.docs[doc]?.selection != null)
  const signature = useSignatures((s) => s.list.find((item) => item.id === s.active))

  if (tool === 'sig-place' && signature) {
    const place = (rect: Rect): void => void addImage(doc, page.id, signature.png, rect, 'signature')
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="rect"
        clickToo
        cursor="copy"
        onRect={place}
        onPoint={(point) => {
          // Un clic estampa la firma con un tamaño natural, centrada en el punto.
          const width = signature.kind === 'initials' ? 60 : 150
          const height = (width * signature.height) / signature.width
          place([point[0] - width / 2, point[1] - height / 2, point[0] + width / 2, point[1] + height / 2])
        }}
      />
    )
  }
  if (tool === 'fill-text') {
    const style = fillStyle(fill.size, fill.color)
    const pad = textPadding(style.size)
    const origin = (point: Point): Point => [point[0] - pad.x, point[1] - pad.y - (style.size * style.lineHeight) / 2]
    return (
      <>
        <DrawLayer pxPerPt={pxPerPt} gesture="point" cursor="text" onPoint={setAdding} />
        {adding && (
          <div className="over-layer" style={{ zIndex: 6 }}>
            <TextEditor
              pxPerPt={pxPerPt}
              x={origin(adding)[0]}
              y={origin(adding)[1]}
              style={style}
              initial=""
              onCancel={() => setAdding(null)}
              onCommit={(text) => {
                const at = origin(adding)
                setAdding(null)
                void addText(doc, page.id, at, text, style)
              }}
            />
          </div>
        )}
      </>
    )
  }
  if (tool === 'check' || tool === 'cross') {
    const size = Math.max(8, fill.size * 1.15)
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="point"
        cursor="copy"
        onPoint={(point) =>
          void addShape(doc, page.id, tool, [point[0] - size / 2, point[1] - size / 2, point[0] + size / 2, point[1] + size / 2], { stroke: fill.color, fill: null, width: 0 }, true)
        }
      />
    )
  }
  if (tool === 'date') {
    const style = fillStyle(fill.size, fill.color)
    const pad = textPadding(style.size)
    return (
      <DrawLayer
        pxPerPt={pxPerPt}
        gesture="point"
        cursor="copy"
        onPoint={(point) =>
          void addText(
            doc,
            page.id,
            [point[0] - pad.x, point[1] - pad.y - (style.size * style.lineHeight) / 2],
            formatDateStyle(dateStyle),
            style
          )
        }
      />
    )
  }
  if (tool === 'cert-rect') {
    return <DrawLayer pxPerPt={pxPerPt} gesture="rect" onRect={(rect) => openCert(doc, { page: page.id, rect })} />
  }
  return (
    <>
      {hasSelection && <div className="over-layer catch" onPointerDown={() => selectObject(doc, page.id, null)} />}
      <ObjectsLayer doc={doc} page={page} pxPerPt={pxPerPt} />
    </>
  )
}

export function SignOptions({ doc }: { doc: DocView }) {
  const fill = useTools((s) => s.fill)
  const setDefaults = useTools((s) => s.set)
  const hasSignature = useSignatures((s) => s.list.length > 0)
  const dateStyle = useApp((s) => s.settings.dateStyle)
  if (doc.mode !== 'sign' || doc.search.open) return null
  if (doc.tool === '' && doc.selection?.kind === 'object') return <ObjectOptions doc={doc} />
  if (['fill-text', 'check', 'cross', 'date'].includes(doc.tool)) {
    return (
      <SubBar>
        {doc.tool === 'date' && (
          <>
            <SubLabel>{L.sign.options.format}</SubLabel>
            <select
              className="select sm"
              aria-label={L.sign.options.dateFormat}
              value={dateStyle}
              onChange={(event) => void updateSettings({ dateStyle: event.target.value as DateStyle })}
            >
              {DATE_STYLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} · {formatDateStyle(option.value)}
                </option>
              ))}
            </select>
          </>
        )}
        <SubLabel>{L.sign.options.size}</SubLabel>
        <NumberField value={fill.size} min={5} max={72} suffix="pt" width={64} tip={L.sign.options.size} onChange={(size) => setDefaults('fill', { size })} />
        <ColorButton value={fill.color} onChange={(color) => color && setDefaults('fill', { color })} tip={L.sign.options.color} />
        <SubLabel>{L.sign.options.clickToPlace}</SubLabel>
      </SubBar>
    )
  }
  if (doc.tool === 'sig-place') {
    return (
      <SubBar>
        <SubLabel>{hasSignature ? L.sign.options.placeSignature : L.sign.options.createFirst}</SubLabel>
      </SubBar>
    )
  }
  if (doc.tool === 'cert-rect') {
    return (
      <SubBar>
        <SubLabel>{L.sign.options.drawCertBox}</SubLabel>
        <button className="btn sm" onClick={() => openCert(doc.id, {})}>
          {L.sign.options.signInvisible}
        </button>
      </SubBar>
    )
  }
  return null
}

// ----------------------------------------------------- crear firma manuscrita

function SignaturePadDialog({ doc }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const setList = useSignatures((s) => s.setList)
  const setActive = useSignatures((s) => s.setActive)
  const [tab, setTab] = useState<'draw' | 'type' | 'image'>('draw')
  const [kind, setKind] = useState<'signature' | 'initials'>('signature')
  const [ink, setInk] = useState(INKS[0][0])
  const [typed, setTyped] = useState('')
  const [font, setFont] = useState(SCRIPT_FONTS[0])
  const [imported, setImported] = useState<SignatureImage | null>(null)
  const [importedUrl, setImportedUrl] = useState<string | null>(null)
  const [hasInk, setHasInk] = useState(false)
  const [save, setSave] = useState(true)
  const canvas = useRef<HTMLCanvasElement>(null)
  const last = useRef<{ x: number; y: number; mx: number; my: number } | null>(null)

  useEffect(() => {
    const element = canvas.current
    if (!element) return
    // Resolución doble para que la firma quede nítida al ampliarla en el PDF.
    element.width = element.clientWidth * 2
    element.height = element.clientHeight * 2
  }, [tab])
  useEffect(() => () => void (importedUrl && URL.revokeObjectURL(importedUrl)), [importedUrl])

  const position = (event: ReactPointerEvent): { x: number; y: number } => {
    const bounds = event.currentTarget.getBoundingClientRect()
    return { x: (event.clientX - bounds.left) * 2, y: (event.clientY - bounds.top) * 2 }
  }
  const down = (event: ReactPointerEvent<HTMLDivElement>): void => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const p = position(event)
    last.current = { ...p, mx: p.x, my: p.y }
    const context = canvas.current?.getContext('2d')
    if (!context) return
    context.fillStyle = ink
    context.beginPath()
    context.arc(p.x, p.y, 2.4, 0, Math.PI * 2)
    context.fill()
    setHasInk(true)
  }
  const move = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const previous = last.current
    const context = canvas.current?.getContext('2d')
    if (!previous || !context) return
    const p = position(event)
    // Trazo suavizado: curva entre los puntos medios de cada tramo.
    const mx = (previous.x + p.x) / 2
    const my = (previous.y + p.y) / 2
    context.strokeStyle = ink
    context.lineWidth = 4.6
    context.lineCap = 'round'
    context.lineJoin = 'round'
    context.beginPath()
    context.moveTo(previous.mx, previous.my)
    context.quadraticCurveTo(previous.x, previous.y, mx, my)
    context.stroke()
    last.current = { ...p, mx, my }
  }
  const clear = (): void => {
    const element = canvas.current
    element?.getContext('2d')?.clearRect(0, 0, element.width, element.height)
    setHasInk(false)
  }

  const build = async (): Promise<SignatureImage | null> => {
    if (tab === 'draw') return canvas.current ? exportTrimmed(canvas.current) : null
    if (tab === 'image') return imported
    const text = typed.trim()
    if (!text) return null
    const surface = document.createElement('canvas')
    surface.width = 1600
    surface.height = 420
    const context = surface.getContext('2d')
    if (!context) return null
    await document.fonts.load(`150px "${font}"`).catch(() => undefined)
    context.fillStyle = ink
    context.font = `150px "${font}", cursive`
    context.textBaseline = 'middle'
    context.fillText(text, 40, 210, 1520)
    return exportTrimmed(surface)
  }

  const ready = tab === 'draw' ? hasInk : tab === 'type' ? typed.trim().length > 0 : imported !== null
  const accept = async (): Promise<void> => {
    const image = await build()
    if (!image) return
    if (save) {
      const list = await window.folio.signatures.add({ kind, png: image.png, width: image.width, height: image.height })
      setList(list)
      setActive(list[0]?.id ?? null)
    } else {
      // Sin guardar: queda solo para esta sesión.
      const temp = { id: `tmp${Date.now()}`, kind, png: image.png, width: image.width, height: image.height, createdAt: Date.now() }
      setList([temp, ...useSignatures.getState().list])
      setActive(temp.id)
    }
    close()
    if (doc) setTool(doc, 'sig-place')
  }

  return (
    <Dialog
      title={L.sign.pad.title}
      size="wide"
      onClose={close}
      footer={
        <>
          <Checkbox checked={save} onChange={setSave}>
            {L.sign.pad.saveForReuse}
          </Checkbox>
          <div className="spacer" />
          <button className="btn" onClick={close}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={!ready} onClick={() => void accept()}>
            {L.sign.pad.use}
          </button>
        </>
      }
    >
      <div className="row">
        <Segmented
          value={tab}
          options={[
            { value: 'draw', label: L.sign.pad.draw },
            { value: 'type', label: L.sign.pad.type },
            { value: 'image', label: L.sign.pad.image }
          ]}
          onChange={setTab}
        />
        <div className="grow" />
        <Segmented
          value={kind}
          options={[
            { value: 'signature', label: L.sign.pad.signature },
            { value: 'initials', label: L.sign.pad.initials }
          ]}
          onChange={setKind}
        />
      </div>
      {tab === 'draw' && (
        <div className="sig-pad" onPointerDown={down} onPointerMove={move} onPointerUp={() => (last.current = null)}>
          <canvas ref={canvas} />
          {!hasInk && <span className="hint">{L.sign.pad.drawHint}</span>}
        </div>
      )}
      {tab === 'type' && (
        <>
          <input className="input" placeholder={L.sign.pad.yourName} value={typed} maxLength={60} data-autofocus onChange={(e) => setTyped(e.target.value)} />
          <div className="sig-typed">
            <span style={{ fontFamily: `"${font}", cursive`, color: ink }}>{typed || L.sign.pad.yourName}</span>
          </div>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            {SCRIPT_FONTS.map((name) => (
              <button key={name} className={cn('btn sm', name === font ? 'primary' : 'ghost')} style={{ fontFamily: `"${name}", cursive` }} onClick={() => setFont(name)}>
                {name}
              </button>
            ))}
          </div>
        </>
      )}
      {tab === 'image' && (
        <>
          <div className="sig-typed">{importedUrl ? <img src={importedUrl} alt={L.sign.pad.importedAlt} style={{ maxWidth: '90%', maxHeight: '90%' }} /> : <span style={{ fontSize: 14, color: '#9aa0ab' }}>{L.sign.pad.imageHint}</span>}</div>
          <div className="row">
            <button
              className="btn"
              onClick={() =>
                void (async () => {
                  try {
                    const picked = unwrap(await window.folio.files.pickImage())
                    if (!picked) return
                    const image = await imageToInk(picked.data)
                    if (!image) {
                      useApp.getState().toast('warning', L.sign.pad.noStrokes)
                      return
                    }
                    setImported(image)
                    setImportedUrl(URL.createObjectURL(new Blob([image.png as Uint8Array<ArrayBuffer>], { type: 'image/png' })))
                  } catch (error) {
                    toastError(error)
                  }
                })()
              }
            >
              {L.sign.pad.pickImage}
            </button>
            <span className="hint">{L.sign.pad.whiteRemoved}</span>
          </div>
        </>
      )}
      {tab !== 'image' && (
        <div className="row">
          <span className="label">{L.sign.pad.ink}</span>
          {INKS.map(([color, name]) => (
            <button
              key={color}
              title={name}
              onClick={() => setInk(color)}
              style={{ width: 22, height: 22, borderRadius: '50%', background: color, boxShadow: ink === color ? `0 0 0 2px var(--bg-surface), 0 0 0 4px ${color}` : 'none' }}
            />
          ))}
          <div className="grow" />
          {tab === 'draw' && (
            <button className="btn sm ghost" disabled={!hasInk} onClick={clear}>
              {L.common.clear}
            </button>
          )}
        </div>
      )}
    </Dialog>
  )
}

function SignatureListDialog() {
  const close = useApp((s) => s.closeDialog)
  const { list, urls, setList } = useSignatures()
  return (
    <Dialog
      title={L.sign.saved.title}
      size="wide"
      onClose={close}
      footer={
        <button className="btn primary" onClick={close}>
          {L.sign.saved.done}
        </button>
      }
    >
      {list.length === 0 ? (
        <p>{L.sign.saved.empty}</p>
      ) : (
        <div className="sig-list">
          {list.map((item) => (
            <div key={item.id} className="sig-card">
              <img src={urls[item.id]} alt={L.sign.saved.alt} />
              <button className="remove" title={L.sign.saved.remove} onClick={() => void window.folio.signatures.remove(item.id).then(setList)}>
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="notice">
        <ShieldCheck size={15} />
        <span>{L.sign.saved.privacy}</span>
      </div>
    </Dialog>
  )
}

// ----------------------------------------------------- firma con certificado

interface Certificate {
  thumbprint: string
  subject: { cn?: string; givenName?: string; surname?: string; serialNumber?: string; organization?: string }
  issuer: { cn?: string; organization?: string }
  notBefore: string
  notAfter: string
  valid: boolean
  canSign: boolean
  algorithm: string
  /** Nombre y documento del titular ya preparados por el motor para mostrarlos. */
  displayName?: string
  displayId?: string | null
  /** Motivos por los que el certificado puede no servir (caducado, clave débil…). */
  issues?: string[]
  hardware?: boolean
}

// Con estos motivos el motor se niega a firmar. «noSigningUsage» no está aquí:
// el motor firma igualmente y lo avisa, así que se deja elegir el certificado.
const BLOCKING_ISSUES = new Set(['expired', 'notYetValid', 'weakKey', 'unsupportedKey'])
const ISSUE_TEXT: Record<string, string> = {
  expired: L.sign.cert.issue.expired,
  notYetValid: L.sign.cert.issue.notYetValid,
  weakKey: L.sign.cert.issue.weakKey,
  unsupportedKey: L.sign.cert.issue.unsupportedKey,
  noSigningUsage: L.sign.cert.issue.noSigningUsage
}

function certUsable(cert: Certificate): boolean {
  if (cert.issues) return !cert.issues.some((issue) => BLOCKING_ISSUES.has(issue))
  return cert.valid && cert.canSign
}

function certName(cert: Certificate | null | undefined): string {
  return cert?.displayName || cert?.subject.cn || ''
}

function CertSignDialog({ doc: docId, data }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useApp((s) => (docId ? s.docs[docId] : undefined))
  const settings = useApp((s) => s.settings)
  const signatures = useSignatures()
  const [certs, setCerts] = useState<Certificate[] | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const [pfx, setPfx] = useState<{ token: string; name: string } | null>(null)
  const [pfxPassword, setPfxPassword] = useState('')
  const [pfxInfo, setPfxInfo] = useState<Certificate | null>(null)
  const [reason, setReason] = useState(settings.signReason)
  const [location, setLocation] = useState(settings.signLocation)
  const [withImage, setWithImage] = useState(false)
  const tsaUrl = timestampUrlOf(settings)
  const [level, setLevel] = useState<SignLevel>(settings.signLevel)
  // Si el sello o la larga duración fallan, se ofrece firmar con el tipo anterior.
  const [fallback, setFallback] = useState<SignLevel | null>(null)
  const effective: SignLevel = tsaUrl ? level : 'basic'
  // Lo que se espera de un documento firmado: que quede cerrado. Se desmarca
  // cuando tiene que firmar otra persona después (un contrato a dos firmas).
  const [lock, setLock] = useState(true)
  const [error, setError] = useState('')
  const [working, setWorking] = useState(false)

  useEffect(() => {
    void signatures.load()
    query<{ certificates: Certificate[] }>('sign.certificates')
      .then((result) => {
        setCerts(result.certificates)
        setChosen(result.certificates.find(certUsable)?.thumbprint ?? null)
      })
      .catch((err) => {
        setCerts([])
        setError(errorMessage(err))
      })
    // Solo al abrir el diálogo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!doc) return null
  const rect = data?.rect as Rect | undefined
  const page = data?.page as string | undefined
  const field = data?.field as string | undefined
  const visible = Boolean(rect || field)
  const handwritten = signatures.list.find((s) => s.id === signatures.active)
  const current = pfx ? pfxInfo : (certs?.find((c) => c.thumbprint === chosen) ?? null)
  const name = certName(current)

  const pickPfx = async (): Promise<void> => {
    try {
      const picked = unwrap(await window.folio.sign.pickPfx())
      if (!picked) return
      setPfx(picked)
      setPfxInfo(null)
      setPfxPassword('')
      setChosen(null)
      setError('')
    } catch (err) {
      toastError(err)
    }
  }
  const checkPfx = async (): Promise<Certificate | null> => {
    if (!pfx) return null
    try {
      const info = unwrap(await window.folio.sign.pfxInfo(pfx.token, pfxPassword)) as Certificate
      setPfxInfo(info)
      setError('')
      return info
    } catch (err) {
      setError(err instanceof FolioError && err.code === 'wrong_password' ? L.sign.cert.wrongPassword : errorMessage(err))
      return null
    }
  }

  const sign = async (override?: SignLevel): Promise<void> => {
    // «chosen» es el certificado; el tipo de firma va aparte.
    const signLevel = override ?? effective
    setWorking(true)
    setError('')
    setFallback(null)
    try {
      if (pfx && !pfxInfo && !(await checkPfx())) return
      const request: Record<string, unknown> = {
        source: pfx ? { type: 'pfx', token: pfx.token, password: pfxPassword } : { type: 'store', thumbprint: chosen },
        appearance: { name: true, date: true, id: true, label: true, reason: Boolean(reason.trim()), location: Boolean(location.trim()), border: false }
      }
      if (reason.trim()) request.reason = reason.trim()
      if (location.trim()) request.location = location.trim()
      if (field) request.field = field
      else if (rect && page) Object.assign(request, { page, rect })
      if (signLevel !== 'basic' && tsaUrl) {
        request.timestampUrl = tsaUrl
        if (signLevel === 'longTerm') request.longTerm = true
      }
      if (lock) request.lock = true
      const image = visible && withImage && handwritten ? handwritten.png : undefined
      const result = unwrap(await window.folio.sign.sign(doc.id, request, image)) as MutationResult & {
        signature?: { warnings?: string[]; locked?: boolean }
      }
      useApp.getState().applyResult(doc.id, result)
      void window.folio.settings
        .set({ signReason: reason.trim(), signLocation: location.trim(), ...(tsaUrl ? { signLevel } : {}) })
        .then((next) => next && useApp.getState().setSettings(next))
      close()
      setTool(doc.id, '')
      useApp.getState().toast('success', L.sign.cert.done)
      // Un solo aviso (varios apilados taparían el documento; el detalle está en
      // «Firmas»): si se pidió bloquear y no se pudo, ese; si no, el primero del
      // motor (p. ej., que el certificado no procede de una autoridad de confianza).
      const warning =
        lock && result.signature?.locked === false
          ? L.sign.cert.notLocked
          : result.signature?.warnings?.[0]
      if (warning) useApp.getState().toast('warning', warning)
      // Lo habitual es guardar el firmado como un archivo aparte.
      void saveDocAs(doc.id, fmt(L.sign.cert.fileName, { name: doc.state.name.replace(/\.pdf$/i, '') }))
    } catch (err) {
      // Cancelar el aviso de Windows (o el PIN) no es un error que mostrar.
      if (!(err instanceof FolioError && err.code === 'cancelled')) {
        setError(errorMessage(err))
        if (err instanceof FolioError && err.code === 'timestamp_failed') setFallback('basic')
        else if (err instanceof FolioError && err.code === 'long_term_failed') setFallback('timestamp')
      }
    } finally {
      setWorking(false)
    }
  }

  const canSign = !working && (pfx ? pfxPassword.length > 0 : Boolean(chosen))
  return (
    <Dialog
      title={L.sign.cert.title}
      size="wide"
      icon={<FileKey size={18} color="var(--accent-fg)" />}
      onClose={close}
      footer={
        <>
          <button className="btn ghost" onClick={() => void pickPfx()}>
            {L.sign.cert.usePfx}
          </button>
          <div className="spacer" />
          <button className="btn" onClick={close}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={!canSign} onClick={() => void sign()}>
            {working ? L.sign.cert.signing : L.sign.cert.sign}
          </button>
        </>
      }
    >
      {pfx ? (
        <>
          <div className="cert on">
            <FileKey size={20} />
            <div className="grow">
              <div className="cert-name">{certName(pfxInfo) || pfx.name}</div>
              <div className="cert-meta">
                {pfxInfo
                  ? `${fmt(L.sign.cert.issuedBy, { issuer: pfxInfo.issuer.cn ?? '—' })} · ${pfxInfo.valid ? fmt(L.sign.cert.validUntil, { date: formatDate(pfxInfo.notAfter) }) : L.sign.cert.notValid}`
                  : L.sign.cert.pfxFile}
              </div>
            </div>
            <button className="btn sm ghost" onClick={() => setPfx(null)}>
              {L.sign.cert.change}
            </button>
          </div>
          <Field label={L.sign.cert.password} hint={L.sign.cert.passwordHint}>
            <input
              className="input"
              type="password"
              autoComplete="off"
              value={pfxPassword}
              onChange={(e) => {
                setPfxPassword(e.target.value)
                setPfxInfo(null)
              }}
              onBlur={() => pfxPassword && void checkPfx()}
            />
          </Field>
        </>
      ) : certs === null ? (
        <div className="row">
          <div className="spinner" />
          <span>{L.sign.cert.searching}</span>
        </div>
      ) : certs.length === 0 ? (
        <div className="notice warn">
          <CircleAlert size={15} />
          <span>{L.sign.cert.none}</span>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 210, overflow: 'auto' }}>
          {certs.map((cert) => {
            const usable = certUsable(cert)
            const issues = (cert.issues ?? []).filter((issue) => issue !== 'expired' && issue !== 'notYetValid')
            return (
              <button key={cert.thumbprint} className={cn('cert', chosen === cert.thumbprint && 'on', !usable && 'off')} disabled={!usable} onClick={() => setChosen(cert.thumbprint)}>
                <FileKey size={20} />
                <div className="grow">
                  <div className="cert-name">
                    {certName(cert) || L.sign.cert.certificate}
                    {cert.displayId ? ` · ${cert.displayId}` : ''}
                  </div>
                  <div className="cert-meta">
                    {cert.issuer.cn ?? cert.issuer.organization ?? '—'} · {cert.valid ? fmt(L.sign.cert.validUntil, { date: formatDate(cert.notAfter) }) : L.sign.cert.notValid}
                    {cert.hardware ? ` · ${L.sign.cert.hardware}` : ''}
                    {issues.map((issue) => ` · ${ISSUE_TEXT[issue] ?? L.sign.cert.issue.other}`).join('')}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      )}

      <div className="form-grid">
        <Field label={L.sign.cert.reason}>
          <input className="input" value={reason} maxLength={200} placeholder={L.sign.cert.reasonPlaceholder} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Field label={L.sign.cert.location}>
          <input className="input" value={location} maxLength={200} onChange={(e) => setLocation(e.target.value)} />
        </Field>
      </div>

      {visible ? (
        <>
          <div className="sig-preview">
            {withImage && handwritten && <img src={signatures.urls[handwritten.id]} alt="" />}
            <div className="lines">
              <span>{L.sign.cert.appearance.signedBy}</span>
              <b>{name || L.sign.pad.yourName}</b>
              {(current?.displayId ?? current?.subject.serialNumber) && <span>{current?.displayId ?? current?.subject.serialNumber}</span>}
              <span>{fmt(L.sign.cert.appearance.date, { date: new Date().toLocaleString(locale) })}</span>
              {reason.trim() && <span>{fmt(L.sign.cert.appearance.reason, { text: reason.trim() })}</span>}
              {location.trim() && <span>{fmt(L.sign.cert.appearance.location, { text: location.trim() })}</span>}
            </div>
          </div>
          <Checkbox checked={withImage && Boolean(handwritten)} disabled={!handwritten} onChange={setWithImage}>
            {handwritten ? L.sign.cert.includeHandwritten : L.sign.cert.includeHandwrittenNone}
          </Checkbox>
        </>
      ) : (
        <div className="notice">
          <ShieldCheck size={15} />
          <span>{L.sign.cert.invisible}</span>
        </div>
      )}
      <Field label={L.sign.cert.level}>
        <div className="sign-levels" role="radiogroup" aria-label={L.sign.cert.level}>
          {SIGN_LEVELS.map((option) => {
            const disabled = option.value !== 'basic' && !tsaUrl
            return (
              <label key={option.value} className={cn('sign-level', effective === option.value && 'on', disabled && 'off')}>
                <input
                  type="radio"
                  name="sign-level"
                  checked={effective === option.value}
                  disabled={disabled}
                  onChange={() => {
                    setLevel(option.value)
                    setFallback(null)
                  }}
                />
                <span>
                  <b>{option.title}</b>
                  <small>{option.text(timestampNameOf(settings))}</small>
                </span>
              </label>
            )
          })}
        </div>
        {!tsaUrl && <p className="hint">{L.sign.cert.noTimestampService}</p>}
      </Field>
      <Checkbox checked={lock} onChange={setLock}>
        {L.sign.cert.lock}
      </Checkbox>
      {!lock && <p className="hint">{L.sign.cert.unlockedHint}</p>}
      {error && (
        <div className="error-text sign-error">
          <span>{error}</span>
          {fallback && (
            <button
              className="btn sm"
              disabled={working}
              onClick={() => {
                setLevel(fallback)
                void sign(fallback)
              }}
            >
              {fallback === 'basic' ? L.sign.cert.withoutTimestamp : L.sign.cert.timestampOnly}
            </button>
          )}
        </div>
      )}
    </Dialog>
  )
}

// ----------------------------------------------------- comprobar firmas

interface SignatureStatus {
  field: string
  type?: 'signature' | 'timestamp'
  signer: { cn?: string; serialNumber?: string; organization?: string }
  issuer: { cn?: string }
  signedAt: string | null
  timestamp: { time: string; valid: boolean; trusted?: boolean } | null
  intact: boolean
  trusted: boolean
  coverage: string
  modifiedAfter: boolean
  summary: 'valid' | 'warning' | 'invalid' | 'unknown'
  messages: string[]
}

const STATUS_ICON = {
  valid: <CircleCheck size={18} />,
  warning: <TriangleAlert size={18} />,
  invalid: <CircleX size={18} />,
  unknown: <CircleAlert size={18} />
}

/** Titular de cada firma. No se llama «válida» a una firma cuyo firmante no se ha podido identificar. */
function statusText(item: SignatureStatus): string {
  const stamp = item.type === 'timestamp'
  switch (item.summary) {
    case 'valid':
      return stamp ? L.sign.check.status.timestampValid : L.sign.check.status.valid
    case 'warning':
      if (stamp) return L.sign.check.status.timestampWarning
      return item.trusted ? L.sign.check.status.warning : L.sign.check.status.unverified
    case 'invalid':
      return stamp ? L.sign.check.status.timestampInvalid : L.sign.check.status.invalid
    default:
      return L.sign.check.status.unknown
  }
}

function SignatureCheckDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const [items, setItems] = useState<SignatureStatus[] | null>(null)
  const [error, setError] = useState('')
  const [online, setOnline] = useState(false)
  const check = (useNetwork: boolean): void => {
    if (!docId) return
    setItems(null)
    setError('')
    query<{ signatures: SignatureStatus[] }>('sign.validate', { doc: docId, online: useNetwork }, { priority: 9 })
      .then((result) => {
        setItems(result.signatures)
        setOnline(useNetwork)
      })
      .catch((err) => {
        setItems([])
        setError(errorMessage(err))
      })
  }
  useEffect(() => {
    // Al abrir se valida sin salir a internet; la consulta en línea solo se
    // hace si el usuario la pide con el botón.
    check(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId])
  return (
    <Dialog
      title={L.sign.check.title}
      size="wide"
      onClose={close}
      footer={
        <>
          {items !== null && items.length > 0 && !online && (
            <button className="btn ghost" title={L.sign.check.revocationTip} onClick={() => check(true)}>
              {L.sign.check.revocation}
            </button>
          )}
          <div className="spacer" />
          <button className="btn primary" onClick={close}>
            {L.common.close}
          </button>
        </>
      }
    >
      {items === null ? (
        <div className="row">
          <div className="spinner" />
          <span>{L.sign.check.checking}</span>
        </div>
      ) : error ? (
        <span className="error-text">{error}</span>
      ) : items.length === 0 ? (
        <p>{L.sign.check.none}</p>
      ) : (
        items.map((item, index) => (
          <div key={index} className={cn('sig-status', item.summary)}>
            {STATUS_ICON[item.summary] ?? STATUS_ICON.unknown}
            <div className="grow">
              <div className="cert-name">
                {item.signer.cn ?? (item.type === 'timestamp' ? L.sign.check.timestampAuthority : L.sign.check.unknownSigner)}
                {/* El número de serie del titular suele llevar un prefijo de tipo y país (IDCES-, PNOES-…). */}
                {item.signer.serialNumber ? ` · ${item.signer.serialNumber.replace(/^(IDC|PNO|PAS|TIN|VAT|NTR)[A-Z]{2}-/, '')}` : ''}
              </div>
              <div className="cert-meta">
                {statusText(item)}
                {item.signedAt ? ` · ${formatDate(item.signedAt)}` : ''}
                {item.issuer.cn ? ` · ${fmt(L.sign.check.issuedBy, { issuer: item.issuer.cn })}` : ''}
              </div>
              {item.messages.length > 0 && (
                <ul>
                  {item.messages.map((message, i) => (
                    <li key={i}>{message}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))
      )}
    </Dialog>
  )
}

registerDialog('signaturePad', SignaturePadDialog)
registerDialog('signatureList', SignatureListDialog)
registerDialog('certSign', CertSignDialog)
registerDialog('signatureCheck', SignatureCheckDialog)
