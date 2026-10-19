import { Sparkles } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { MutationResult, Rect } from '@shared/types'
import { type CustomDialogProps, registerDialog } from '@/components/Dialogs'
import { Dialog } from '@/components/ui'
import { fmt, L, plural } from '@/i18n'
import { call, errorMessage, isCancelled } from '@/lib/api'
import { viewerOf } from '@/lib/viewerControl'
import { mutate } from '@/state/actions'
import { aiHas, useAi } from '@/state/ai'
import { useApp } from '@/state/app'

/**
 * Censurar → «Datos personales»: el motor propone lo que parece un dato
 * personal (reglas y, con la IA activada, el modelo de entidades) y el usuario
 * elige qué se marca. No se marca nada sin su confirmación, y nada se borra
 * hasta que aplica la censura (como con cualquier otra marca).
 */

interface Candidate {
  id: string
  page: string
  kind: keyof typeof L.ai.redact.kinds
  text: string
  rects: Rect[]
  source: 'rule' | 'ai'
}

function PersonalDataDialog({ doc: docId }: CustomDialogProps) {
  const close = useApp((s) => s.closeDialog)
  const doc = useApp((s) => (docId ? s.docs[docId] : undefined))
  const status = useAi((s) => s.status)
  const [found, setFound] = useState<{ candidates: Candidate[]; ai: boolean; truncated: boolean } | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [progress, setProgress] = useState(0)
  const T = L.ai.redact

  useEffect(() => {
    if (!docId) return
    const controller = new AbortController()
    void call<{ candidates: Candidate[]; ai: boolean; truncated: boolean }>(
      'redact.candidates',
      { doc: docId, useAi: aiHas(status, 'ner') },
      { signal: controller.signal, priority: 7, onProgress: (info) => setProgress(info.total ? info.done / info.total : 0) }
    )
      .then((reply) => {
        setFound(reply.result)
        setSelected(new Set(reply.result.candidates.map((c) => c.id)))
      })
      .catch((error: unknown) => {
        if (!isCancelled(error)) {
          useApp.getState().toast('error', errorMessage(error))
          close()
        }
      })
    return () => controller.abort()
    // Solo al abrir el cuadro.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId])

  const groups = useMemo(() => {
    const out = new Map<string, Candidate[]>()
    for (const item of found?.candidates ?? []) {
      const list = out.get(item.kind) ?? []
      list.push(item)
      out.set(item.kind, list)
    }
    return [...out.entries()]
  }, [found])

  if (!doc || !docId) return null
  const pageNumber = (page: string): number => doc.pages.findIndex((p) => p.id === page) + 1
  const toggle = (id: string, on: boolean): void =>
    setSelected((current) => {
      const next = new Set(current)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  const mark = async (): Promise<void> => {
    const byPage = new Map<string, Rect[]>()
    for (const item of found?.candidates ?? []) {
      if (!selected.has(item.id)) continue
      byPage.set(item.page, [...(byPage.get(item.page) ?? []), ...item.rects])
    }
    let ok = 0
    for (const [page, rects] of byPage) {
      // De 200 en 200: el motor acepta un número limitado de zonas por llamada.
      for (let start = 0; start < rects.length; start += 200) {
        const result = await mutate<MutationResult>(docId, 'redact.mark', { page, rects: rects.slice(start, start + 200) })
        if (result) ok++
      }
    }
    close()
    if (ok) useApp.getState().toast('success', plural(T.marked, selected.size))
  }

  return (
    <Dialog
      title={T.title}
      size="wide"
      onClose={close}
      footer={
        <>
          {found && found.candidates.length > 0 && (
            <div className="row grow">
              <button className="btn sm ghost" onClick={() => setSelected(new Set(found.candidates.map((c) => c.id)))}>
                {T.all}
              </button>
              <button className="btn sm ghost" onClick={() => setSelected(new Set())}>
                {T.nothing}
              </button>
            </div>
          )}
          <button className="btn" onClick={close}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={!found || selected.size === 0} onClick={() => void mark()}>
            {fmt(T.mark, { n: selected.size })}
          </button>
        </>
      }
    >
      <p className="hint">{T.intro}</p>
      {!found ? (
        <div className="ai-searching">
          <span className="hint">{T.searching}</span>
          <div className="progress">
            <div style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        </div>
      ) : found.candidates.length === 0 ? (
        <p>{T.none}</p>
      ) : (
        <div className="ai-candidates">
          {groups.map(([kind, items]) => (
            <section key={kind}>
              <h3>
                {T.kinds[kind as Candidate['kind']] ?? kind} <span className="hint">({items.length})</span>
              </h3>
              <ul>
                {items.map((item) => (
                  <li key={item.id}>
                    <label className="check">
                      <input type="checkbox" checked={selected.has(item.id)} onChange={(event) => toggle(item.id, event.target.checked)} />
                      <span className="ai-cand-text">{item.text}</span>
                    </label>
                    {item.source === 'ai' && (
                      <span className="ai-badge small">
                        <Sparkles size={10} /> {T.fromAi}
                      </span>
                    )}
                    <button
                      className="ai-cite"
                      onClick={() => {
                        const rect = item.rects[0]
                        if (rect) viewerOf(docId)?.reveal(item.page, rect)
                      }}
                    >
                      {fmt(T.pageShort, { n: pageNumber(item.page) })}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {found && <p className="hint">{found.ai ? T.aiUsed : T.aiOff}</p>}
      {found?.truncated && <p className="hint">{fmt(T.truncated, { n: found.candidates.length })}</p>}
    </Dialog>
  )
}

registerDialog('personalData', PersonalDataDialog)
