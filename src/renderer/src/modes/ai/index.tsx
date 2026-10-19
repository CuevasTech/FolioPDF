import {
  AlignJustify,
  AlignLeft,
  ArrowRight,
  ArrowUp,
  Bot,
  Check,
  Copy,
  FileText,
  Languages,
  MessageSquarePlus,
  MessageSquareText,
  NotebookPen,
  Search,
  Settings2,
  Sparkles,
  Square,
  TextSelect,
  Type
} from 'lucide-react'
import { type FormEvent, type KeyboardEvent, type ReactNode, useLayoutEffect, useRef, useState } from 'react'
import { create } from 'zustand'
import type { MutationResult } from '@shared/types'
import { fmt, L } from '@/i18n'
import { call, errorMessage, isCancelled } from '@/lib/api'
import { cn } from '@/lib/util'
import { viewerOf } from '@/lib/viewerControl'
import { readSelection } from '@/modes/annotate/selection'
import { mutate } from '@/state/actions'
import { aiHas, aiReady, useAi } from '@/state/ai'
import { type DocView, useApp } from '@/state/app'

/**
 * El asistente de IA (modo «IA»): resumir, preguntar, buscar por significado y
 * transformar texto. Todo lo hace el motor con los modelos de este equipo.
 *
 * Lo que escribe la IA se muestra SIEMPRE como texto: las citas «[p.N]» se
 * convierten en botones y los «**negrita**» en <strong>, pero nunca se
 * interpreta HTML (OWASP LLM05: la respuesta no es de fiar). Junto a cada
 * resultado se dice que lo ha generado una IA (Reglamento de IA, art. 50.1).
 */

type Tab = 'summary' | 'ask' | 'search' | 'text'

interface Answer {
  text: string
  injection?: number
  removedCitations?: number
  partial?: { covered: number; total: number }
  condensed?: boolean
}
interface Turn {
  question: string
  answer: Answer | null
  error?: string
}
interface SearchHit {
  page: string
  number: number
  text: string
  rects: number[][]
}
interface PanelData {
  tab: Tab
  summary: Answer | null
  turns: Turn[]
  hits: SearchHit[] | null
  query: string
  input: string
  output: Answer | null
}

const EMPTY: PanelData = { tab: 'summary', summary: null, turns: [], hits: null, query: '', input: '', output: null }

/** Lo del panel de cada documento se conserva al cambiar de modo. */
const usePanels = create<{ panels: Record<string, PanelData> }>(() => ({ panels: {} }))
const panelOf = (doc: string): PanelData => usePanels.getState().panels[doc] ?? EMPTY
const patchPanel = (doc: string, patch: Partial<PanelData>): void =>
  usePanels.setState((state) => ({ panels: { ...state.panels, [doc]: { ...(state.panels[doc] ?? EMPTY), ...patch } } }))

// ------------------------------------------------------------ texto con citas

const CITATION = /\[p\.(\d{1,5})\]/g

function goToPage(doc: DocView, number: number): void {
  const page = doc.pages[number - 1]
  if (page) viewerOf(doc.id)?.scrollToPage(page.id)
}

/** Una línea: citas como botones, **negritas** como <strong>, el resto texto. */
function Line({ doc, text }: { doc: DocView; text: string }) {
  const parts: ReactNode[] = []
  let key = 0
  for (const [index, piece] of text.split(/(\*\*[^*]+\*\*)/).entries()) {
    if (index % 2 === 1) {
      parts.push(<strong key={key++}>{piece.slice(2, -2)}</strong>)
      continue
    }
    let last = 0
    for (const match of piece.matchAll(CITATION)) {
      if (match.index > last) parts.push(piece.slice(last, match.index))
      const number = Number(match[1])
      parts.push(
        <button key={key++} className="ai-cite" data-tip={fmt(L.ai.panel.goToPage, { n: number })} onClick={() => goToPage(doc, number)}>
          {fmt(L.ai.panel.pageChip, { n: number })}
        </button>
      )
      last = match.index + match[0].length
    }
    if (last < piece.length) parts.push(piece.slice(last))
  }
  return <>{parts}</>
}

function AiText({ doc, text, streaming }: { doc: DocView; text: string; streaming?: boolean }) {
  // Las listas de los modelos («* algo», «- algo») se ven como viñetas.
  const lines = text.split('\n').map((line) => line.replace(/^(\s*)[*-]\s+/, '$1• '))
  return (
    <div className={cn('ai-text', streaming && 'streaming')}>
      {lines.map((line, index) => (
        <p key={index}>
          <Line doc={doc} text={line} />
        </p>
      ))}
    </div>
  )
}

function Notes({ answer }: { answer: Answer }) {
  return (
    <>
      {answer.condensed && <p className="hint">{L.ai.summary.condensed}</p>}
      {answer.partial && <p className="hint">{fmt(L.ai.summary.partial, answer.partial)}</p>}
      {Boolean(answer.injection) && <p className="ai-warning">{L.ai.panel.injection}</p>}
      {Boolean(answer.removedCitations) && <p className="hint">{L.ai.panel.removedCitations}</p>}
    </>
  )
}

/** Copiar y guardar como nota (marcada en el PDF como generada por IA). */
/** ``side``: hacia dónde salen las etiquetas (a la izquierda si van pegadas al borde derecho). */
function ResultActions({ doc, text, side }: { doc: DocView; text: string; side?: 'left' }) {
  const [copied, setCopied] = useState(false)
  const saveNote = async (): Promise<void> => {
    const page = viewerOf(doc.id)?.center()?.page ?? doc.pages[0]?.id
    if (!page) return
    const result = await mutate<MutationResult>(doc.id, 'ai.addNote', { page, text: text.slice(0, 20_000) })
    if (result) {
      const number = doc.pages.findIndex((p) => p.id === page) + 1
      useApp.getState().toast('success', fmt(L.ai.panel.noteSaved, { n: number }))
    }
  }
  const copyLabel = copied ? L.ai.panel.copied : L.ai.panel.copy
  return (
    <div className="ai-actions">
      <button
        className="icon-btn sm"
        aria-label={copyLabel}
        data-tip={copyLabel}
        data-tip-side={side}
        onClick={() => {
          void navigator.clipboard.writeText(text)
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
      {!doc.state.signed && (
        <button className="icon-btn sm" aria-label={L.ai.panel.saveNote} data-tip={L.ai.panel.saveNote} data-tip-side={side} onClick={() => void saveNote()}>
          <NotebookPen size={14} />
        </button>
      )}
    </div>
  )
}

/** Un resultado terminado: el texto, sus avisos y, al pie, que es de una IA y las acciones. */
function Result({ doc, answer, label }: { doc: DocView; answer: Answer; label?: string }) {
  return (
    <div className="ai-result">
      {label && <b className="ai-label">{label}</b>}
      <AiText doc={doc} text={answer.text} />
      <Notes answer={answer} />
      <div className="ai-result-foot">
        <span className="ai-disclaimer">{L.ai.panel.disclaimer}</span>
        <ResultActions doc={doc} text={answer.text} side="left" />
      </div>
    </div>
  )
}

/** Una llamada a la IA con su texto parcial y la posibilidad de detenerla. */
function useRun() {
  const [partial, setPartial] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const run = async <T,>(method: string, params: Record<string, unknown>): Promise<T | null> => {
    controller.current?.abort()
    const own = new AbortController()
    controller.current = own
    setBusy(true)
    setPartial('')
    setMessage('')
    try {
      const reply = await call<T>(method, params, {
        signal: own.signal,
        priority: 6,
        onProgress: (info) => {
          if (typeof info.text === 'string') setPartial(info.text)
          if (info.message) setMessage(info.message)
        }
      })
      return reply.result
    } catch (error) {
      if (!isCancelled(error)) useApp.getState().toast('error', errorMessage(error))
      return null
    } finally {
      if (controller.current === own) {
        controller.current = null
        setBusy(false)
        setPartial(null)
      }
    }
  }
  const stop = (): void => controller.current?.abort()
  return { run, stop, busy, partial, message }
}

/** En qué está la IA («Pensando…»), con un botón pequeño para detenerla. */
function Working({ message, onStop }: { message: string; onStop?: () => void }) {
  return (
    <div className="ai-working" role="status">
      <span className="ai-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="grow">{message || L.ai.panel.working}</span>
      {onStop && (
        <button className="ai-stop" aria-label={L.ai.panel.stop} data-tip={L.ai.panel.stop} data-tip-side="left" onClick={onStop}>
          <Square size={10} fill="currentColor" />
        </button>
      )}
    </div>
  )
}

/** Mientras la IA trabaja: su estado y lo que va escribiendo. */
function Running({ doc, run }: { doc: DocView; run: ReturnType<typeof useRun> }) {
  return (
    <div className="ai-result running">
      <Working message={run.message} onStop={run.stop} />
      {run.partial ? <AiText doc={doc} text={run.partial} streaming /> : null}
    </div>
  )
}

/** Un cuadro de texto que crece con lo escrito (hasta ``max`` píxeles). */
function useAutoGrow(value: string, max: number) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const area = ref.current
    if (!area) return
    area.style.height = 'auto'
    area.style.height = `${Math.min(area.scrollHeight, max)}px`
  }, [value, max])
  return ref
}

// ------------------------------------------------------------------ pestañas

function SummaryTab({ doc }: { doc: DocView }) {
  const summary = usePanels((s) => s.panels[doc.id]?.summary ?? null)
  const job = useRun()
  const summarize = async (length: 'short' | 'long'): Promise<void> => {
    const result = await job.run<Answer & { coveredPages?: number; totalPages?: number }>('ai.summarize', { doc: doc.id, length })
    if (result) {
      const partialInfo =
        !result.condensed && result.coveredPages && result.totalPages && result.coveredPages < result.totalPages
          ? { covered: result.coveredPages, total: result.totalPages }
          : undefined
      patchPanel(doc.id, { summary: { ...result, partial: partialInfo } })
    }
  }
  return (
    <div className="ai-tab">
      <p className="hint">{L.ai.summary.intro}</p>
      <div className="ai-pair">
        <button className="btn sm primary" disabled={job.busy} onClick={() => void summarize('short')}>
          <AlignLeft size={14} />
          {L.ai.summary.short}
        </button>
        <button className="btn sm" disabled={job.busy} onClick={() => void summarize('long')}>
          <AlignJustify size={14} />
          {L.ai.summary.long}
        </button>
      </div>
      {job.busy && <Running doc={doc} run={job} />}
      {!job.busy && summary && <Result doc={doc} answer={summary} />}
    </div>
  )
}

function AskTab({ doc }: { doc: DocView }) {
  const turns = usePanels((s) => s.panels[doc.id]?.turns ?? [])
  const [question, setQuestion] = useState('')
  const job = useRun()
  const area = useAutoGrow(question, 140)
  const ask = async (): Promise<void> => {
    const text = question.trim()
    if (!text || job.busy) return
    setQuestion('')
    const index = panelOf(doc.id).turns.length
    patchPanel(doc.id, { turns: [...panelOf(doc.id).turns, { question: text, answer: null }] })
    const result = await job.run<Answer>('ai.ask', { doc: doc.id, question: text.slice(0, 2000) })
    const current = [...panelOf(doc.id).turns]
    current[index] = { question: text, answer: result, error: result ? undefined : '' }
    patchPanel(doc.id, { turns: current })
  }
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void ask()
    }
  }
  return (
    <div className="ai-tab ai-chat">
      {/* Aviso permanente de que se habla con una IA (Reglamento de IA, art. 50.1). */}
      <p className="ai-notice">
        <Bot size={14} />
        <span>{L.ai.ask.notice}</span>
      </p>
      <div className="ai-turns">
        {turns.length === 0 && <p className="hint">{L.ai.ask.example}</p>}
        {turns.map((turn, index) => (
          <div key={index} className="ai-turn">
            <div className="ai-q">
              <span>{turn.question}</span>
            </div>
            <div className="ai-a">
              <b>
                <Sparkles size={12} /> {L.ai.ask.assistant}
              </b>
              {turn.answer ? (
                <>
                  <AiText doc={doc} text={turn.answer.text} />
                  <Notes answer={turn.answer} />
                  <ResultActions doc={doc} text={turn.answer.text} />
                </>
              ) : job.busy && index === turns.length - 1 ? (
                job.partial ? (
                  <AiText doc={doc} text={job.partial} streaming />
                ) : (
                  <Working message={job.message} />
                )
              ) : null}
            </div>
          </div>
        ))}
      </div>
      <div className="ai-ask">
        <div className="ai-composer">
          <textarea
            ref={area}
            rows={1}
            maxLength={2000}
            value={question}
            placeholder={L.ai.ask.placeholder}
            aria-label={L.ai.ask.placeholder}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKey}
          />
          <div className="ai-composer-bar">
            {turns.length > 0 && !job.busy && (
              <button className="ai-link" onClick={() => patchPanel(doc.id, { turns: [] })}>
                <MessageSquarePlus size={13} />
                {L.ai.ask.clear}
              </button>
            )}
            <span className="grow" />
            {job.busy ? (
              <button className="ai-send stop" aria-label={L.ai.panel.stop} data-tip={L.ai.panel.stop} data-tip-side="left" onClick={job.stop}>
                <Square size={10} fill="currentColor" />
              </button>
            ) : (
              <button className="ai-send" aria-label={L.ai.ask.send} data-tip={L.ai.ask.send} data-tip-side="left" disabled={!question.trim()} onClick={() => void ask()}>
                <ArrowUp size={15} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function SearchTab({ doc }: { doc: DocView }) {
  const hits = usePanels((s) => s.panels[doc.id]?.hits ?? null)
  const query = usePanels((s) => s.panels[doc.id]?.query ?? '')
  const job = useRun()
  const search = async (event?: FormEvent): Promise<void> => {
    event?.preventDefault()
    const text = query.trim()
    if (!text || job.busy) return
    const result = await job.run<{ results: SearchHit[] }>('ai.search', { doc: doc.id, query: text.slice(0, 2000), k: 10 })
    if (result) patchPanel(doc.id, { hits: result.results })
  }
  return (
    <div className="ai-tab">
      <p className="hint">{L.ai.search.intro}</p>
      <form className="ai-field ai-search" onSubmit={(event) => void search(event)}>
        <Search size={14} />
        <input
          type="search"
          value={query}
          maxLength={2000}
          placeholder={L.ai.search.placeholder}
          aria-label={L.ai.search.placeholder}
          onChange={(event) => patchPanel(doc.id, { query: event.target.value })}
        />
        <button type="submit" className="ai-send" aria-label={L.ai.search.run} data-tip={L.ai.search.run} data-tip-side="left" disabled={!query.trim()}>
          {job.busy ? <span className="spinner sm" /> : <ArrowRight size={14} />}
        </button>
      </form>
      {hits && hits.length === 0 && <p className="hint">{L.ai.search.empty}</p>}
      {hits && hits.length > 0 && (
        <ul className="ai-hits">
          {hits.map((hit, index) => (
            <li key={index}>
              <button
                onClick={() => {
                  const rect = hit.rects[0]
                  if (rect) viewerOf(doc.id)?.reveal(hit.page, rect as [number, number, number, number])
                  else viewerOf(doc.id)?.scrollToPage(hit.page)
                }}
              >
                <span className="ai-hit-page">{fmt(L.ai.panel.pageChip, { n: hit.number })}</span>
                <span className="ai-hit-text">{hit.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type Action = 'translate-es' | 'translate-en' | 'rewrite' | 'formal' | 'simple' | 'shorter' | 'fix'

const ACTIONS: { id: Action; label: string; icon?: ReactNode }[] = [
  { id: 'translate-es', label: L.ai.text.translateEs, icon: <Languages size={13} /> },
  { id: 'translate-en', label: L.ai.text.translateEn, icon: <Languages size={13} /> },
  { id: 'rewrite', label: L.ai.text.rewrite },
  { id: 'formal', label: L.ai.text.formal },
  { id: 'simple', label: L.ai.text.simple },
  { id: 'shorter', label: L.ai.text.shorter },
  { id: 'fix', label: L.ai.text.fix }
]

function TextTab({ doc }: { doc: DocView }) {
  const input = usePanels((s) => s.panels[doc.id]?.input ?? '')
  const output = usePanels((s) => s.panels[doc.id]?.output ?? null)
  const job = useRun()
  const area = useAutoGrow(input, 220)
  const useSelection = (): void => {
    const selected = readSelection(doc.id)
    if (selected?.text.trim()) patchPanel(doc.id, { input: selected.text.slice(0, 8000) })
    else useApp.getState().toast('info', L.ai.text.noSelection)
  }
  const transform = async (action: Action): Promise<void> => {
    const text = input.trim()
    if (!text) return
    const params =
      action === 'translate-es' || action === 'translate-en'
        ? { action: 'translate', target: action.slice(-2) }
        : { action }
    const result = await job.run<Answer>('ai.transform', { doc: doc.id, text: text.slice(0, 8000), ...params })
    if (result) patchPanel(doc.id, { output: result })
  }
  return (
    <div className="ai-tab">
      <p className="hint">{L.ai.text.intro}</p>
      <div className="ai-composer">
        <textarea
          ref={area}
          rows={3}
          maxLength={8000}
          value={input}
          placeholder={L.ai.text.placeholder}
          aria-label={L.ai.text.placeholder}
          onChange={(event) => patchPanel(doc.id, { input: event.target.value })}
        />
        <div className="ai-composer-bar">
          <button className="ai-link" onMouseDown={(event) => event.preventDefault()} onClick={useSelection}>
            <TextSelect size={13} />
            {L.ai.text.useSelection}
          </button>
        </div>
      </div>
      <div className="ai-chips">
        {ACTIONS.map((action) => (
          <button key={action.id} className="ai-chip" disabled={job.busy || !input.trim()} onClick={() => void transform(action.id)}>
            {action.icon}
            {action.label}
          </button>
        ))}
      </div>
      {job.busy && <Running doc={doc} run={job} />}
      {!job.busy && output && <Result doc={doc} answer={output} label={L.ai.text.result} />}
    </div>
  )
}

const TABS: { id: Tab; label: string; icon: ReactNode; needs: string }[] = [
  { id: 'summary', label: L.ai.panel.tabs.summary, icon: <FileText size={15} />, needs: 'llm' },
  { id: 'ask', label: L.ai.panel.tabs.ask, icon: <MessageSquareText size={15} />, needs: 'llm' },
  { id: 'search', label: L.ai.panel.tabs.search, icon: <Search size={15} />, needs: 'embed' },
  { id: 'text', label: L.ai.panel.tabs.text, icon: <Type size={15} />, needs: 'llm' }
]

export function AiPanel({ doc }: { doc: DocView }) {
  const status = useAi((s) => s.status)
  const tab = usePanels((s) => s.panels[doc.id]?.tab ?? 'summary')
  if (doc.mode !== 'ai') return null
  const ready = aiReady(status)
  const openSettings = (): void => {
    setSettingsTab('ai')
    useApp.getState().openDialog({ type: 'settings' })
  }
  return (
    <aside className="inspector ai-panel" aria-label={L.ai.panel.title}>
      <div className="inspector-head">
        <Sparkles size={16} />
        <span className="grow">{L.ai.panel.title}</span>
        <span className="ai-badge">{L.ai.badge}</span>
      </div>
      <div className="inspector-body">
        {!ready ? (
          <div className="ai-tab">
            <p className="hint">{status?.tier === 'off' || !status ? L.ai.panel.off : L.ai.panel.notReady}</p>
            <button className="btn sm" onClick={openSettings}>
              <Settings2 size={14} />
              {L.ai.panel.openSettings}
            </button>
          </div>
        ) : (
          <>
            {/* Solo la pestaña elegida lleva su nombre; las demás lo muestran al pasar por encima. */}
            <div className="ai-tabs" role="tablist">
              {TABS.filter((item) => aiHas(status, item.needs)).map((item) => (
                <button
                  key={item.id}
                  role="tab"
                  aria-selected={item.id === tab}
                  className={cn(item.id === tab && 'on')}
                  onClick={() => patchPanel(doc.id, { tab: item.id })}
                >
                  {item.icon}
                  <span className="ai-tab-label">{item.label}</span>
                </button>
              ))}
            </div>
            {tab === 'summary' && <SummaryTab doc={doc} />}
            {tab === 'ask' && <AskTab doc={doc} />}
            {tab === 'search' && <SearchTab doc={doc} />}
            {tab === 'text' && <TextTab doc={doc} />}
          </>
        )}
      </div>
    </aside>
  )
}

// La pestaña de Ajustes que se abre la próxima vez (la pone el panel).
let nextSettingsTab: string | null = null
export function setSettingsTab(tab: string): void {
  nextSettingsTab = tab
}
export function takeSettingsTab(): string | null {
  const tab = nextSettingsTab
  nextSettingsTab = null
  return tab
}
