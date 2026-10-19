import { BadgeCheck, Lock, X } from 'lucide-react'
import { useState } from 'react'
import { L } from '@/i18n'
import { type DocView, useApp } from '@/state/app'

const dismissed = new Set<string>()

/** Lo que deja hacer la firma (nivel de bloqueo del motor). */
const LOCK_TEXT: Record<number, string> = {
  1: L.shell.banner.locked,
  2: L.shell.banner.lockedForm,
  3: L.shell.banner.lockedComments
}

/** Aviso discreto sobre el estado del documento: firmado o con restricciones. */
export function DocBanner({ doc }: { doc: DocView }) {
  const openDialog = useApp((s) => s.openDialog)
  const [, refresh] = useState(0)
  if (doc.mode !== 'view' || doc.search.open) return null

  const restricted = doc.info.encrypted && !doc.info.owner
  const kind = doc.state.signed ? 'signed' : restricted ? 'restricted' : null
  if (!kind || dismissed.has(`${doc.id}:${kind}`)) return null
  const close = (): void => {
    dismissed.add(`${doc.id}:${kind}`)
    refresh((n) => n + 1)
  }

  return (
    <div className="banner">
      {kind === 'signed' ? <BadgeCheck size={16} /> : <Lock size={16} />}
      <span>
        {kind === 'signed'
          ? doc.state.signaturesIntact
            ? LOCK_TEXT[doc.state.lock?.level ?? 0] ?? L.shell.banner.signed
            : L.shell.banner.invalidated
          : L.shell.banner.restricted}
      </span>
      {kind === 'signed' ? (
        <button className="btn sm" onClick={() => openDialog({ type: 'custom', id: 'signatureCheck', doc: doc.id })}>
          {L.shell.banner.viewSignatures}
        </button>
      ) : (
        <button className="btn sm" onClick={() => openDialog({ type: 'unlock', doc: doc.id })}>
          {L.shell.banner.unlock}
        </button>
      )}
      <button className="icon-btn sm" aria-label={L.shell.dismiss} onClick={close}>
        <X size={14} />
      </button>
    </div>
  )
}
