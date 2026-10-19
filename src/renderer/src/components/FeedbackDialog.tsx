import { Send } from 'lucide-react'
import { useEffect, useState } from 'react'
import { L } from '@/i18n'
import { errorMessage, unwrap } from '@/lib/api'
import { useApp } from '@/state/app'
import { registerDialog } from './Dialogs'
import { Dialog, Segmented } from './ui'

/**
 * Enviar un comentario al autor: tipo (fallo, mejora u otra cosa) y mensaje.
 * Llega por correo mediante su servicio de comentarios (src/main/feedback.ts);
 * si esta copia no lo tiene, se ofrece GitHub.
 */

type Kind = 'Fallo' | 'Mejora' | 'Otra cosa'

const PLACEHOLDER: Record<Kind, string> = {
  Fallo: L.feedback.placeholder.bug,
  Mejora: L.feedback.placeholder.idea,
  'Otra cosa': L.feedback.placeholder.other
}

function FeedbackDialog() {
  const close = useApp((s) => s.closeDialog)
  const toast = useApp((s) => s.toast)
  const [available, setAvailable] = useState<boolean | null>(null)
  const [kind, setKind] = useState<Kind>('Fallo')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void window.folio.feedback
      .available()
      .then(setAvailable)
      .catch(() => setAvailable(false))
  }, [])

  const send = async (): Promise<void> => {
    setSending(true)
    setError('')
    try {
      unwrap(await window.folio.feedback.send(kind, message.trim()))
      toast('success', L.feedback.thanks)
      close()
    } catch (failure) {
      setError(errorMessage(failure))
      setSending(false)
    }
  }

  const github = (
    <button className="btn ghost" onClick={() => void window.folio.about.open('issues')}>
      {L.feedback.viewOnGitHub}
    </button>
  )

  if (available === false) {
    return (
      <Dialog
        title={L.feedback.title}
        onClose={close}
        footer={
          <>
            <button className="btn" onClick={close}>
              {L.common.close}
            </button>
            <button className="btn primary" onClick={() => void window.folio.about.open('issues').then(close)}>
              {L.feedback.openGitHub}
            </button>
          </>
        }
      >
        <p>{L.feedback.githubText}</p>
      </Dialog>
    )
  }

  return (
    <Dialog
      title={L.feedback.title}
      onClose={close}
      footer={
        <>
          {github}
          <span className="spacer" />
          <button className="btn" onClick={close}>
            {L.common.cancel}
          </button>
          <button className="btn primary" disabled={message.trim().length < 3 || sending || !available} onClick={() => void send()}>
            <Send size={15} />
            {sending ? L.feedback.sending : L.feedback.send}
          </button>
        </>
      }
    >
      <Segmented
        value={kind}
        full
        options={[
          { value: 'Fallo', label: L.feedback.kind.bug },
          { value: 'Mejora', label: L.feedback.kind.idea },
          { value: 'Otra cosa', label: L.feedback.kind.other }
        ]}
        onChange={setKind}
      />
      <textarea
        className="textarea feedback-text"
        aria-label={L.feedback.message}
        placeholder={PLACEHOLDER[kind]}
        value={message}
        maxLength={4000}
        data-autofocus
        disabled={sending}
        onChange={(event) => setMessage(event.target.value)}
      />
      <span className="hint">{L.feedback.hint}</span>
      {error && <span className="error-text">{error}</span>}
    </Dialog>
  )
}

registerDialog('feedback', FeedbackDialog)
