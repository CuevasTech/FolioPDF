import { Crop, ImagePlus, RefreshCw, RotateCcw, RotateCw, ScanLine, Trash } from 'lucide-react'
import { type KeyboardEvent, type PointerEvent, useCallback, useEffect, useRef, useState } from 'react'
import type { ScanAdded, ScannerInfo, ScanOptions, ScanPageInfo, ScanStyle } from '@shared/api'
import { fmt, L, plural } from '@/i18n'
import { call, errorMessage, isCancelled, query, trackProgress, unwrap } from '@/lib/api'
import { cn } from '@/lib/util'
import { handleOutcomes } from '@/state/actions'
import { aiHas, useAi } from '@/state/ai'
import { useApp } from '@/state/app'
import { type CustomDialogProps, registerDialog } from './Dialogs'
import { Checkbox, Dialog, Field, Segmented } from './ui'

/**
 * Digitalizar: fotos de papeles (las del móvil, pasadas al ordenador) o páginas
 * del escáner, hechas un PDF. Folio busca el papel en cada foto, lo endereza,
 * quita las sombras y deja el fondo blanco; aquí se revisa cada página antes de
 * crear el documento (girarla, ajustar sus esquinas, cambiar el orden, quitarla).
 * El trabajo lo hace el motor (engine/folio_engine/ops/scan.py).
 */

const THUMB = 360
const FULL: number[][] = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1]
]

const STYLES: { value: ScanStyle; label: string }[] = [
  { value: 'auto', label: L.scan.styles.auto },
  { value: 'color', label: L.scan.styles.color },
  { value: 'gray', label: L.scan.styles.gray },
  { value: 'bw', label: L.scan.styles.bw },
  { value: 'original', label: L.scan.styles.original }
]

const STYLE_HINTS: Record<ScanStyle, string> = L.scan.styleHints

// Lo último elegido para el escáner se recuerda mientras Folio siga abierto.
let lastScanner: Omit<ScanOptions, 'device'> & { device: string } = { device: '', source: 'flatbed', color: 'color', dpi: 200 }

/** Cuadrilátero convexo: lo mismo que comprueba el motor antes de aceptarlo. */
function convex(quad: number[][]): boolean {
  const signs = quad.map((p, i) => {
    const q = quad[(i + 1) % 4]
    const r = quad[(i + 2) % 4]
    return (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])
  })
  return signs.every((s) => s > 0) || signs.every((s) => s < 0)
}

/** Ajustar a mano las esquinas del papel sobre la foto entera. */
function EdgeEditor({
  job,
  page,
  onApply,
  onCancel
}: {
  job: string
  page: ScanPageInfo
  onApply: (change: { quad?: number[][]; whole?: boolean; redetect?: boolean }) => void
  onCancel: () => void
}) {
  const [source, setSource] = useState<{ url: string; w: number; h: number } | null>(null)
  const [quad, setQuad] = useState<number[][]>(page.quad ?? FULL)
  const [room, setRoom] = useState<{ w: number; h: number } | null>(null)
  const svg = useRef<SVGSVGElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const dragging = useRef<number | null>(null)

  // La foto, lo más grande que quepa en el hueco sin deformarse.
  useEffect(() => {
    const box = stage.current
    if (!box) return
    const observer = new ResizeObserver(() => setRoom({ w: box.clientWidth, h: box.clientHeight }))
    observer.observe(box)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    let url = ''
    let alive = true
    call<{ w: number; h: number }>('scan.preview', { job, page: page.id, view: 'source', size: 1100 }, { priority: 9 })
      .then((reply) => {
        if (!alive || !reply.binary) return
        url = URL.createObjectURL(new Blob([reply.binary as BlobPart], { type: 'image/jpeg' }))
        setSource({ url, w: reply.result.w, h: reply.result.h })
      })
      .catch(() => undefined)
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [job, page.id])

  const move = (index: number, x: number, y: number): void =>
    setQuad((current) => current.map((p, i) => (i === index ? [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))] : p)))

  const onPointerMove = (event: PointerEvent<SVGSVGElement>): void => {
    if (dragging.current === null || !svg.current) return
    const box = svg.current.getBoundingClientRect()
    move(dragging.current, (event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height)
  }
  // Con el teclado: cada esquina se puede elegir con Tab y mover con las flechas.
  const onKey = (index: number) => (event: KeyboardEvent<SVGCircleElement>) => {
    const step = event.shiftKey ? 0.02 : 0.004
    const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    const d = delta[event.key]
    if (!d) return
    event.preventDefault()
    move(index, quad[index][0] + d[0], quad[index][1] + d[1])
  }

  const valid = convex(quad)
  const w = source?.w ?? 1
  const h = source?.h ?? 1
  const points = quad.map(([x, y]) => `${x * w},${y * h}`).join(' ')
  const radius = Math.max(w, h) * 0.014
  const T = L.scan.edges
  const corners = [T.topLeft, T.topRight, T.bottomRight, T.bottomLeft]
  const fit = room && source ? Math.min(room.w / source.w, room.h / source.h) : 0
  return (
    <div className="scan-edit">
      <p className="scan-edit-hint">{T.hint}</p>
      <div ref={stage} className="scan-edit-stage">
        {source && fit > 0 ? (
          <div className="scan-edit-photo" style={{ width: source.w * fit, height: source.h * fit }}>
            <img src={source.url} alt={T.photo} draggable={false} />
            <svg
              ref={svg}
              viewBox={`0 0 ${w} ${h}`}
              preserveAspectRatio="none"
              onPointerMove={onPointerMove}
              onPointerUp={() => (dragging.current = null)}
              onPointerCancel={() => (dragging.current = null)}
            >
              <path className="scan-edit-shade" fillRule="evenodd" d={`M0,0H${w}V${h}H0Z M${points.replace(/ /g, ' L')}Z`} />
              <polygon className={cn('scan-edit-quad', !valid && 'bad')} points={points} />
              {quad.map(([x, y], index) => (
                <circle
                  key={index}
                  className="scan-edit-handle"
                  cx={x * w}
                  cy={y * h}
                  r={radius}
                  tabIndex={0}
                  role="slider"
                  aria-label={corners[index]}
                  aria-valuetext={fmt(T.position, { x: Math.round(x * 100), y: Math.round(y * 100) })}
                  onKeyDown={onKey(index)}
                  onPointerDown={(event) => {
                    dragging.current = index
                    svg.current?.setPointerCapture(event.pointerId)
                  }}
                />
              ))}
            </svg>
          </div>
        ) : (
          <span className="scan-empty-text">{T.loading}</span>
        )}
      </div>
      <div className="scan-edit-actions">
        <button className="btn sm ghost" onClick={() => setQuad(FULL)}>
          {T.whole}
        </button>
        {page.found && (
          <button className="btn sm ghost" onClick={() => onApply({ redetect: true })}>
            {T.detected}
          </button>
        )}
        <span className="spacer" />
        {!valid && <span className="error-text">{T.crossed}</span>}
        <button className="btn sm" onClick={onCancel}>
          {L.common.cancel}
        </button>
        <button
          className="btn sm primary"
          disabled={!valid}
          onClick={() => onApply(quad.every((p, i) => p[0] === FULL[i][0] && p[1] === FULL[i][1]) ? { whole: true } : { quad })}
        >
          {L.common.apply}
        </button>
      </div>
    </div>
  )
}

function ScanDialog(_props: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const [job, setJob] = useState<string | null>(null)
  const [pages, setPages] = useState<ScanPageInfo[]>([])
  const [previews, setPreviews] = useState<Record<string, string>>({})
  const [style, setStyle] = useState<ScanStyle>('auto')
  const [ocr, setOcr] = useState(true)
  // Aplanar páginas curvadas: solo con la IA lista (el modelo de aplanado descargado).
  const canUnwarp = useAi((s) => aiHas(s.status, 'unwarp'))
  const [unwarp, setUnwarp] = useState(false)
  const unwarpRef = useRef(false)
  const [ocrAvailable, setOcrAvailable] = useState<boolean | null>(null)
  const [devices, setDevices] = useState<ScannerInfo[] | null>(null)
  const [scanner, setScanner] = useState(lastScanner)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [dragged, setDragged] = useState<string | null>(null)
  const callId = useRef<number | null>(null)
  const jobRef = useRef<string | null>(null)
  const built = useRef(false)
  const urls = useRef<Record<string, string>>({})
  const versions = useRef<Record<string, number>>({})

  // --- vistas previas (JPEG del motor como URL local, que se suelta al cambiar)
  const refresh = useCallback(async (jobId: string, ids: string[], finish: ScanStyle) => {
    for (const id of ids) {
      const version = (versions.current[id] ?? 0) + 1
      versions.current[id] = version
      try {
        const reply = await call<{ w: number; h: number }>(
          'scan.preview',
          { job: jobId, page: id, style: finish, size: THUMB, unwarp: unwarpRef.current },
          { priority: 8 }
        )
        if (versions.current[id] !== version || !reply.binary) continue
        const url = URL.createObjectURL(new Blob([reply.binary as BlobPart], { type: 'image/jpeg' }))
        if (urls.current[id]) URL.revokeObjectURL(urls.current[id])
        urls.current[id] = url
        setPreviews((current) => ({ ...current, [id]: url }))
      } catch {
        /* la página ya no existe (se ha quitado) */
      }
    }
  }, [])

  useEffect(() => {
    void query<{ available: boolean }>('tools.ocrStatus')
      .then((status) => {
        setOcrAvailable(status.available)
        if (!status.available) setOcr(false)
      })
      .catch(() => setOcrAvailable(false))
    void loadDevices()
    return () => {
      for (const url of Object.values(urls.current)) URL.revokeObjectURL(url)
      // Cerrar sin crear el PDF tira las fotos del trabajo.
      if (jobRef.current && !built.current) void query('scan.discard', { job: jobRef.current }).catch(() => undefined)
    }
    // Solo al abrir.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadDevices = async (): Promise<void> => {
    setDevices(null)
    try {
      const list = unwrap(await window.folio.scan.devices())
      setDevices(list)
      setScanner((current) => (list.some((d) => d.id === current.device) ? current : { ...current, device: list[0]?.id ?? '' }))
    } catch {
      setDevices([])
    }
  }

  const run = async (message: string, work: (callId: number) => Promise<void>): Promise<void> => {
    setError('')
    setNotice('')
    setBusy(message)
    const tracker = trackProgress((info) => info.message && setBusy(info.message))
    callId.current = tracker.callId
    try {
      await work(tracker.callId)
    } catch (failure) {
      if (!isCancelled(failure)) setError(errorMessage(failure))
    } finally {
      tracker.done()
      callId.current = null
      setBusy(null)
    }
  }

  const accept = (added: ScanAdded, photos: boolean): void => {
    jobRef.current = added.job
    setJob(added.job)
    setPages((current) => [...current, ...added.pages])
    // Solo en fotos: lo del escáner ya llega recto y no se recorta.
    if (photos && !added.detected) setNotice(L.scan.notices.noEdges)
    else if (photos && added.pages.some((p) => !p.found)) setNotice(L.scan.notices.someWhole)
    void refresh(added.job, added.pages.map((p) => p.id), style)
  }

  const addPhotos = (): Promise<void> =>
    run(L.scan.busy.photos, async (id) => {
      const added = unwrap(await window.folio.scan.fromImages(jobRef.current, id))
      if (added) accept(added, true)
    })

  const scan = (): Promise<void> => {
    lastScanner = scanner
    return run(L.scan.busy.scanning, async (id) => {
      accept(unwrap(await window.folio.scan.fromScanner(jobRef.current, scanner, id)), false)
    })
  }

  const update = async (page: ScanPageInfo, change: Record<string, unknown>): Promise<void> => {
    if (!job) return
    try {
      const next = await query<ScanPageInfo>('scan.update', { job, page: page.id, ...change }, { priority: 9 })
      setPages((current) => current.map((p) => (p.id === page.id ? next : p)))
      await refresh(job, [page.id], style)
    } catch (failure) {
      setError(errorMessage(failure))
    }
  }

  const remove = (page: ScanPageInfo): void => {
    setPages((current) => current.filter((p) => p.id !== page.id))
    if (urls.current[page.id]) URL.revokeObjectURL(urls.current[page.id])
    delete urls.current[page.id]
  }

  const changeStyle = (next: ScanStyle): void => {
    setStyle(next)
    if (job) void refresh(job, pages.map((p) => p.id), next)
  }

  // Con el teclado: Alt+flechas la mueven y Supr la quita.
  const onPageKey = (page: ScanPageInfo) => (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Delete') {
      event.preventDefault()
      remove(page)
      return
    }
    const step = event.altKey && (event.key === 'ArrowLeft' || event.key === 'ArrowUp') ? -1 : event.altKey && (event.key === 'ArrowRight' || event.key === 'ArrowDown') ? 1 : 0
    if (!step) return
    event.preventDefault()
    const element = event.currentTarget
    setPages((current) => {
      const from = current.findIndex((p) => p.id === page.id)
      const to = from + step
      if (from < 0 || to < 0 || to >= current.length) return current
      const next = [...current]
      next.splice(to, 0, next.splice(from, 1)[0])
      return next
    })
    // La página sigue elegida en su nuevo sitio.
    requestAnimationFrame(() => element.focus())
  }

  // Arrastrar una página sobre otra la pone en su sitio.
  const drop = (target: string): void => {
    if (!dragged || dragged === target) return
    setPages((current) => {
      const from = current.findIndex((p) => p.id === dragged)
      const to = current.findIndex((p) => p.id === target)
      if (from < 0 || to < 0) return current
      const next = [...current]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
    setDragged(null)
  }

  const create = (): Promise<void> =>
    run(ocr ? L.scan.busy.creatingOcr : L.scan.busy.creating, async (id) => {
      if (!job) return
      const outcome = unwrap(await window.folio.scan.build(job, pages.map((p) => p.id), style, ocr, id, unwarpRef.current))
      built.current = true
      close()
      handleOutcomes([outcome])
    })

  const cancel = (): void => {
    if (callId.current !== null) window.folio.cancel(callId.current)
    else close()
  }

  const editPage = pages.find((p) => p.id === editing)
  const canScan = devices !== null && devices.length > 0 && scanner.device !== ''
  const T = L.scan
  return (
    <Dialog
      title={T.title}
      className="scan-dialog"
      onClose={() => {
        // Con el editor de esquinas abierto, Esc solo sale de él (no se pierden las fotos).
        if (editing !== null) {
          setEditing(null)
          return
        }
        if (callId.current !== null) window.folio.cancel(callId.current)
        close()
      }}
      footer={
        <>
          <span className="scan-status" aria-live="polite">
            {busy ?? (pages.length > 0 ? plural(L.common.pageCount, pages.length) : '')}
          </span>
          <span className="spacer" />
          <button className="btn" onClick={cancel}>
            {busy ? T.stop : L.common.cancel}
          </button>
          <button className="btn primary" disabled={pages.length === 0 || busy !== null || editing !== null} onClick={() => void create()}>
            {T.create}
          </button>
        </>
      }
    >
      <div className="scan-layout">
        <div className="scan-pages" aria-label={L.common.pages}>
          {editPage && job ? (
            <EdgeEditor
              job={job}
              page={editPage}
              onCancel={() => setEditing(null)}
              onApply={(change) => {
                setEditing(null)
                void update(editPage, change)
              }}
            />
          ) : pages.length === 0 ? (
            <div className="scan-empty">
              <ScanLine size={34} />
              <b>{T.empty.title}</b>
              <span>{T.empty.text}</span>
            </div>
          ) : (
            <div className="scan-grid">
              {pages.map((page, index) => (
                <div
                  key={page.id}
                  className={cn('scan-page', dragged === page.id && 'dragging')}
                  tabIndex={0}
                  aria-label={fmt(T.page.label, { n: index + 1 })}
                  onKeyDown={onPageKey(page)}
                  draggable
                  onDragStart={() => setDragged(page.id)}
                  onDragEnd={() => setDragged(null)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => drop(page.id)}
                >
                  <div className="scan-thumb">
                    {previews[page.id] ? <img src={previews[page.id]} alt={fmt(T.page.alt, { n: index + 1 })} draggable={false} /> : <span className="scan-loading" />}
                    <div className="scan-tools">
                      <button className="icon-btn sm" data-tip={T.page.rotateLeft} aria-label={T.page.rotateLeft} onClick={() => void update(page, { rotate: (page.rotate + 270) % 360 })}>
                        <RotateCcw size={14} />
                      </button>
                      <button className="icon-btn sm" data-tip={T.page.rotateRight} aria-label={T.page.rotateRight} onClick={() => void update(page, { rotate: (page.rotate + 90) % 360 })}>
                        <RotateCw size={14} />
                      </button>
                      <button className="icon-btn sm" data-tip={T.page.edges} aria-label={T.page.edges} onClick={() => setEditing(page.id)}>
                        <Crop size={14} />
                      </button>
                      <button className="icon-btn sm" data-tip={L.common.remove} aria-label={T.page.remove} onClick={() => remove(page)}>
                        <Trash size={14} />
                      </button>
                    </div>
                  </div>
                  <span className="scan-number">{index + 1}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="scan-options">
          <section className="print-group" aria-label={T.photos.title}>
            <h3>{T.photos.title}</h3>
            <button className="btn" disabled={busy !== null || editing !== null} onClick={() => void addPhotos()}>
              <ImagePlus size={15} />
              {T.photos.add}
            </button>
            <p className="scan-hint">{T.photos.hint}</p>
          </section>

          <section className="print-group" aria-label={T.scanner.title}>
            <h3>{T.scanner.title}</h3>
            {devices === null ? (
              <span className="scan-hint">{T.scanner.searching}</span>
            ) : devices.length === 0 ? (
              <div className="scan-none">
                <span className="scan-hint">{T.scanner.none}</span>
                <button className="btn sm ghost" onClick={() => void loadDevices()}>
                  <RefreshCw size={13} />
                  {T.scanner.searchAgain}
                </button>
              </div>
            ) : (
              <>
                <div className="print-row">
                  <select
                    className="select"
                    aria-label={T.scanner.title}
                    value={scanner.device}
                    disabled={busy !== null}
                    onChange={(event) => setScanner({ ...scanner, device: event.target.value })}
                  >
                    {devices.map((device) => (
                      <option key={device.id} value={device.id}>
                        {device.name}
                      </option>
                    ))}
                  </select>
                  <button className="icon-btn" data-tip={T.scanner.refresh} aria-label={T.scanner.refresh} onClick={() => void loadDevices()}>
                    <RefreshCw size={14} />
                  </button>
                </div>
                <Field label={T.scanner.paper}>
                  <Segmented
                    value={scanner.source}
                    full
                    options={[
                      { value: 'flatbed', label: T.scanner.flatbed },
                      { value: 'feeder', label: T.scanner.feeder },
                      { value: 'duplex', label: T.scanner.duplex }
                    ]}
                    onChange={(source) => setScanner({ ...scanner, source })}
                  />
                </Field>
                <div className="print-row">
                  <Field label={T.scanner.color}>
                    <select className="select" aria-label={T.scanner.colorLabel} value={scanner.color} onChange={(event) => setScanner({ ...scanner, color: event.target.value as ScanOptions['color'] })}>
                      <option value="color">{T.scanner.color}</option>
                      <option value="gray">{T.scanner.gray}</option>
                      <option value="bw">{T.scanner.bw}</option>
                    </select>
                  </Field>
                  <Field label={T.scanner.resolution}>
                    <select className="select" aria-label={T.scanner.resolution} value={scanner.dpi} onChange={(event) => setScanner({ ...scanner, dpi: Number(event.target.value) as ScanOptions['dpi'] })}>
                      {[150, 200, 300].map((dpi) => (
                        <option key={dpi} value={dpi}>
                          {fmt(T.scanner.dpi, { n: dpi })}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <button className="btn" disabled={!canScan || busy !== null || editing !== null} onClick={() => void scan()}>
                  <ScanLine size={15} />
                  {pages.length > 0 ? T.scanner.scanMore : T.scanner.scan}
                </button>
              </>
            )}
          </section>

          <section className="print-group" aria-label={T.finish.title}>
            <h3>{T.finish.title}</h3>
            <Segmented value={style} full options={STYLES} onChange={changeStyle} />
            <p className="scan-hint">{STYLE_HINTS[style]}</p>
            <Checkbox checked={ocr} disabled={!ocrAvailable} onChange={setOcr}>
              {T.finish.ocr}
            </Checkbox>
            {ocrAvailable === false && <span className="scan-hint">{T.finish.ocrUnavailable}</span>}
            {canUnwarp && (
              <>
                <Checkbox
                  checked={unwarp}
                  onChange={(value) => {
                    setUnwarp(value)
                    unwarpRef.current = value
                    if (job) void refresh(job, pages.map((p) => p.id), style)
                  }}
                >
                  {L.ai.scan.unwarp}
                </Checkbox>
                <span className="scan-hint">{L.ai.scan.unwarpHint}</span>
              </>
            )}
          </section>

          {notice && <p className="notice">{notice}</p>}
          {error && <span className="error-text">{error}</span>}
        </div>
      </div>
    </Dialog>
  )
}

registerDialog('scan', ScanDialog)

export function openScan(): void {
  useApp.getState().openDialog({ type: 'custom', id: 'scan' })
}
